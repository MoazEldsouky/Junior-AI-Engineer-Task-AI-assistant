"""
Agent Core — the ReAct reasoning loop.

Orchestrates the interaction between the user, the LLM, and the tools.
Follows a Reason → Act → Observe loop with explicit reasoning traces.

Confirmation Flow (structurally enforced)
-----------------------------------------
Any mutation tool (insert_data, update_data, delete_data, undo_change)
returns requires_confirmation=True.  The agent calls
session.request_confirmation() which transitions the session to
AgentState.AWAITING_CONFIRMATION.

While the session is in that state:
  • session.is_tool_blocked(name) returns True for every mutating tool,
    so the LLM *cannot* sneak past confirmation by calling the tool again.
  • The only code path that can reach the actual DataManager write is
    _execute_confirmed_mutation(), which first calls session.begin_commit()
    — and begin_commit() raises RuntimeError if the session is NOT in
    AWAITING_CONFIRMATION state.

Therefore: IDLE → AWAITING_CONFIRMATION → COMMITTING → IDLE
                                        ↑
                                  only path to a write
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import BaseLLMProvider, LLMResponse, get_provider
from app.tools.base import ToolRegistry, ToolResult
from app.agent.session import Session, Message, PendingConfirmation, AgentState
from app.agent.prompt import build_system_prompt
from app.data.manager import DataManager
from app.data.validator import extend_enum, RangeProposal
from app.logging.logger import InteractionLogger
from app.config import settings

import logging

logger = logging.getLogger("agent")


@dataclass
class ReasoningStep:
    """A single step in the agent's reasoning process."""
    step: int
    thought: str | None = None
    action: str | None = None
    action_input: dict | None = None
    observation: str | None = None


@dataclass
class AgentResponse:
    """The full response from the agent."""
    session_id: str
    response: str
    reasoning_steps: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_preview: str | None = None


class Agent:
    """
    The core AI agent — drives the ReAct loop.

    Flow:
    1. User sends a message
    2. Agent adds it to session history
    3. Sends history + tool schemas to the LLM
    4. LLM returns text and/or tool calls
    5. If tool calls → execute tools → feed results back → repeat
    6. If text only → return as final answer
    7. If tool requires confirmation → pause; session transitions to
       AWAITING_CONFIRMATION; mutating tools are blocked until user confirms.
    """

    def __init__(
        self,
        llm: BaseLLMProvider,
        tool_registry: ToolRegistry,
        data_manager: DataManager,
        logger: InteractionLogger,
        max_iterations: int = 10,
        max_history: int = 20,
    ):
        self.llm = llm
        self.tools = tool_registry
        self.dm = data_manager
        self.logger = logger
        self.max_iterations = max_iterations
        self.max_history = max_history

        # Build system prompt with current schemas
        schemas = []
        for ds_info in self.dm.list_datasets():
            schemas.append(self.dm.get_schema(ds_info["key"]))
        self.system_prompt = build_system_prompt(schemas)

        # Cache tool schemas — they don't change at runtime
        self._tool_schemas = self.tools.get_schemas()

    async def process_message(
        self, session: Session, user_message: str
    ) -> AgentResponse:
        """Process a user message through the ReAct loop."""
        start_time = time.time()
        reasoning_steps: list[dict] = []
        tool_call_records: list[dict] = []

        # Ensure system prompt is in history
        if not session.history or session.history[0].role != "system":
            session.add_message(Message(role="system", content=self.system_prompt))

        # -----------------------------------------------------------------
        # If the session is AWAITING_CONFIRMATION, route to the confirmation
        # handler regardless of what the user said.  The LLM has no way to
        # bypass this — the state is checked here, not inside a tool.
        # -----------------------------------------------------------------
        if session.is_awaiting_confirmation:
            return await self._handle_confirmation(
                session, user_message, start_time
            )

        # Add user message to history
        session.add_message(Message(role="user", content=user_message))

        # ReAct loop
        final_response = ""
        requires_confirmation = False
        confirmation_preview = None

        for iteration in range(self.max_iterations):
            step_num = iteration + 1

            # Get LLM response — use cached tool schemas
            messages = session.get_llm_messages(self.max_history)

            try:
                llm_response = await self.llm.generate(messages, self._tool_schemas)
            except Exception as e:
                final_response = f"I encountered an error communicating with the LLM: {str(e)}"
                reasoning_steps.append({
                    "step": step_num,
                    "thought": "LLM communication error",
                    "error": str(e),
                })
                break

            # Case 1: LLM returns tool calls
            if llm_response.has_tool_calls:
                # Record the assistant's tool call message
                tc_dicts = [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    for tc in llm_response.tool_calls
                ]
                session.add_message(
                    Message(
                        role="assistant",
                        content=llm_response.content or "",
                        tool_calls=tc_dicts,
                    )
                )

                # Execute each tool call
                for tc in llm_response.tool_calls:
                    # ----------------------------------------------------------
                    # Structural guard: if a mutating tool is called while we
                    # are AWAITING_CONFIRMATION, block it outright.  This can
                    # only happen if the LLM hallucinates another tool call
                    # without the user answering first.
                    # ----------------------------------------------------------
                    if session.is_tool_blocked(tc.name):
                        blocked_msg = (
                            f"⚠️ Tool '{tc.name}' is blocked: a confirmation is already "
                            "pending for a previous operation. Please answer yes or no first."
                        )
                        session.add_message(
                            Message(
                                role="tool",
                                content=blocked_msg,
                                name=tc.name,
                                tool_call_id=tc.id,
                            )
                        )
                        reasoning_steps.append({
                            "step": step_num,
                            "type": "blocked",
                            "thought": "Mutating tool call blocked — confirmation still pending.",
                            "action": tc.name,
                            "observation": blocked_msg,
                        })
                        # Surface the still-pending confirmation back to the user
                        requires_confirmation = True
                        confirmation_preview = session.pending_confirmation.preview if session.pending_confirmation else None
                        final_response = blocked_msg
                        break

                    tool = self.tools.get(tc.name)
                    if not tool:
                        result = ToolResult(
                            success=False,
                            message=f"Unknown tool: {tc.name}",
                        )
                    else:
                        try:
                            result = tool.execute(**tc.arguments)
                        except Exception as e:
                            result = ToolResult(
                                success=False,
                                message=f"Tool execution error: {e}",
                            )

                    # Build compact observation for the LLM
                    observation_for_llm = self._compact_observation(result)

                    # Record reasoning step (LangChain-style trace)
                    reasoning_steps.append({
                        "step": step_num,
                        "type": "action",
                        "thought": llm_response.content or f"Invoking tool '{tc.name}' to process the request.",
                        "action": f"{tc.name}({json.dumps(tc.arguments, default=str)})",
                        "action_tool": tc.name,
                        "action_input": tc.arguments,
                        "observation": result.message,
                    })

                    tool_call_records.append({
                        "tool": tc.name,
                        "input": tc.arguments,
                        "output": result.message,
                        "success": result.success,
                    })

                    # Check if tool requires confirmation — transition state machine
                    if result.requires_confirmation:
                        op = result.data.get("operation", "unknown") if result.data else "unknown"
                        ds = result.data.get("dataset", "") if result.data else ""
                        pending = PendingConfirmation(
                            operation=op,
                            dataset=ds,
                            data=result.data if result.data else {},
                            preview=result.preview or result.message,
                        )
                        # Structural transition: IDLE → AWAITING_CONFIRMATION
                        session.request_confirmation(pending)

                        requires_confirmation = True
                        confirmation_preview = result.preview or result.message
                        final_response = result.message

                        # Add tool result to history
                        session.add_message(
                            Message(
                                role="tool",
                                content=result.message,
                                name=tc.name,
                                tool_call_id=tc.id,
                            )
                        )
                        break  # Stop loop — wait for user confirmation

                    # Add compact tool result to history for next iteration
                    session.add_message(
                        Message(
                            role="tool",
                            content=observation_for_llm,
                            name=tc.name,
                            tool_call_id=tc.id,
                        )
                    )

                if requires_confirmation:
                    break

            # Case 2: LLM returns text only (final answer)
            else:
                final_response = llm_response.content or "I'm not sure how to answer that."
                reasoning_steps.append({
                    "step": step_num,
                    "type": "finish",
                    "thought": "Data gathered. Composing final answer for the user.",
                    "action": None,
                    "observation": final_response[:200] + ("..." if len(final_response) > 200 else ""),
                })
                session.add_message(
                    Message(role="assistant", content=final_response)
                )
                break
        else:
            # Max iterations reached
            final_response = "I've reached the maximum number of reasoning steps. Please try simplifying your request."

        # Log the interaction
        latency_ms = int((time.time() - start_time) * 1000)
        self.logger.log_interaction(
            session_id=session.session_id,
            user_query=user_message,
            reasoning_steps=reasoning_steps,
            tool_calls=tool_call_records,
            final_response=final_response,
            latency_ms=latency_ms,
            llm_provider=str(self.llm),
        )

        return AgentResponse(
            session_id=session.session_id,
            response=final_response,
            reasoning_steps=reasoning_steps,
            tool_calls=tool_call_records,
            requires_confirmation=requires_confirmation,
            confirmation_preview=confirmation_preview,
        )

    def _compact_observation(self, result: ToolResult) -> str:
        """
        Build a compact observation string for the LLM.

        For query results with many rows, truncate to MAX_OBSERVATION_ROWS
        and include a summary. This dramatically reduces token count in
        multi-turn tool conversations while preserving accuracy.
        """
        if not result.data:
            return result.message

        data = result.data

        # Handle query results with row data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            rows = data["data"]
            total = data.get("total_matching", len(rows))

            if len(rows) <= settings.max_observation_rows:
                return json.dumps(data, default=str)

            # Large result set — truncate and summarize
            max_rows = settings.max_observation_rows
            compact = {
                "total_matching": total,
                "rows_returned": len(rows),
                "showing_first": max_rows,
                "data": rows[:max_rows],
                "note": f"Showing {min(max_rows, len(rows))} of {total} total rows.",
            }
            return json.dumps(compact, default=str)

        # For aggregation results, schema inspections, etc. — pass through
        return json.dumps(data, default=str)

    async def _handle_confirmation(
        self, session: Session, user_message: str, start_time: float
    ) -> AgentResponse:
        """
        Handle a user's yes/no response to a pending confirmation.

        This is the ONLY code path that can reach _execute_confirmed_mutation.
        It calls session.begin_commit() first, which raises RuntimeError if
        the session is not in AWAITING_CONFIRMATION — a second structural guard.

        After a confirmed mutation, the agent re-enters the ReAct loop so the
        LLM can query and display the affected record(s) in a markdown table.
        """
        pending = session.pending_confirmation
        session.add_message(Message(role="user", content=user_message))

        normalized = user_message.strip().lower()
        confirmed = normalized in ("yes", "y", "confirm", "proceed", "ok", "sure", "do it", "go ahead")
        declined = normalized in ("no", "n", "cancel", "abort", "stop", "nevermind", "never mind")

        reasoning_steps = []
        tool_calls = []

        if not confirmed and not declined:
            # Broader substring check
            if any(word in normalized for word in ["yes", "confirm", "proceed", "sure", "ok"]):
                confirmed = True
            elif any(word in normalized for word in ["no", "cancel", "don't", "stop"]):
                declined = True
            else:
                response = "I need a clear yes or no. Would you like to proceed with the change?"
                session.add_message(Message(role="assistant", content=response))
                return AgentResponse(
                    session_id=session.session_id,
                    response=response,
                    requires_confirmation=True,
                    confirmation_preview=pending.preview if pending else None,
                )

        if confirmed:
            # Structural transition: AWAITING_CONFIRMATION → COMMITTING
            # begin_commit() raises RuntimeError if state is wrong — impossible
            # to call this without going through request_confirmation() first.
            session.begin_commit()
            mutation_result = self._execute_confirmed_mutation(pending)
            session.finish_commit()  # COMMITTING → IDLE

            reasoning_steps.append({
                "step": 1,
                "thought": "User confirmed the operation",
                "action": f"execute_{pending.operation}",
                "observation": mutation_result,
            })

            # -----------------------------------------------------------------
            # Re-enter the ReAct loop so the LLM can query and display the
            # affected record(s) in a markdown table, as required by the
            # system prompt. We inject the mutation result as an assistant
            # message, then add a system-level follow-up instruction telling
            # the LLM exactly which dataset/filters to use for the re-query.
            # -----------------------------------------------------------------
            session.add_message(Message(
                role="assistant",
                content=mutation_result,
            ))

            dataset = pending.data.get("dataset", "")
            op = pending.operation

            if op == "insert":
                rows = pending.data.get("rows", [])
                follow_up = (
                    f"[SYSTEM] Mutation succeeded: {mutation_result}\n"
                    f"The inserted row(s) contained these values: {json.dumps(rows, default=str)}.\n"
                    f"Now use query_data on dataset '{dataset}' to fetch the affected "
                    f"record(s) by their ID and display them in a markdown table as required."
                )
            elif op == "update":
                filters = pending.data.get("filters", [])
                follow_up = (
                    f"[SYSTEM] Mutation succeeded: {mutation_result}\n"
                    f"The updated record(s) match these filters: {json.dumps(filters, default=str)}.\n"
                    f"Now use query_data on dataset '{dataset}' with those same filters "
                    f"to fetch the current state of the affected record(s) and display "
                    f"them in a markdown table as required."
                )
            elif op == "delete":
                follow_up = (
                    f"[SYSTEM] Mutation succeeded: {mutation_result}\n"
                    f"The record(s) have been permanently deleted. "
                    f"Confirm the deletion to the user with the success message only — "
                    f"do not attempt to query deleted records."
                )
            elif op == "undo":
                # For undo, we try to surface the reverted records
                follow_up = (
                    f"[SYSTEM] Mutation succeeded: {mutation_result}\n"
                    f"The undo operation has reverted the previous change on dataset '{dataset}'. "
                    f"Use query_data on dataset '{dataset}' to fetch and display the reverted "
                    f"record(s) in a markdown table as required."
                )
            elif op == "add_column":
                column_name = pending.data.get("column_name", "")
                follow_up = (
                    f"[SYSTEM] Mutation succeeded: {mutation_result}\n"
                    f"Column '{column_name}' has been added to all rows in dataset '{dataset}'. "
                    f"Use query_data on dataset '{dataset}' with columns including '{column_name}' "
                    f"to show a sample of rows with the new column in a markdown table."
                )
            else:
                follow_up = (
                    f"[SYSTEM] Mutation succeeded: {mutation_result}\n"
                    f"Confirm the result to the user."
                )

            session.add_message(Message(role="user", content=follow_up))

            # Run a mini ReAct loop so the LLM fetches and renders the table
            final_response = mutation_result  # fallback if loop produces nothing

            for iteration in range(self.max_iterations):
                step_num = iteration + 2  # continues from step 1 above

                messages = session.get_llm_messages(self.max_history)
                try:
                    llm_response = await self.llm.generate(messages, self._tool_schemas)
                except Exception as e:
                    final_response = (
                        f"{mutation_result}\n\n"
                        f"(Could not fetch the updated record: {e})"
                    )
                    break

                if llm_response.has_tool_calls:
                    tc_dicts = [
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in llm_response.tool_calls
                    ]
                    session.add_message(
                        Message(
                            role="assistant",
                            content=llm_response.content or "",
                            tool_calls=tc_dicts,
                        )
                    )

                    for tc in llm_response.tool_calls:
                        tool = self.tools.get(tc.name)
                        if not tool:
                            result = ToolResult(
                                success=False,
                                message=f"Unknown tool: {tc.name}",
                            )
                        else:
                            try:
                                result = tool.execute(**tc.arguments)
                            except Exception as e:
                                result = ToolResult(
                                    success=False,
                                    message=f"Tool execution error: {e}",
                                )

                        observation_for_llm = self._compact_observation(result)

                        reasoning_steps.append({
                            "step": step_num,
                            "type": "action",
                            "thought": llm_response.content or "Fetching affected record(s) to display as table.",
                            "action": f"{tc.name}({json.dumps(tc.arguments, default=str)})",
                            "action_tool": tc.name,
                            "action_input": tc.arguments,
                            "observation": result.message,
                        })

                        tool_calls.append({
                            "tool": tc.name,
                            "input": tc.arguments,
                            "output": result.message,
                            "success": result.success,
                        })

                        session.add_message(
                            Message(
                                role="tool",
                                content=observation_for_llm,
                                name=tc.name,
                                tool_call_id=tc.id,
                            )
                        )

                else:
                    # Prepend the success message so the action ID is always visible,
                    # then append the LLM's formatted table below it.
                    llm_content = llm_response.content or ""
                    final_response = f"{mutation_result}\n\n{llm_content}".strip()
                    
                    reasoning_steps.append({
                        "step": step_num,
                        "type": "finish",
                        "thought": "Displaying affected record(s) after confirmed mutation.",
                        "action": None,
                        "observation": final_response[:200] + ("..." if len(final_response) > 200 else ""),
                    })
                    session.add_message(
                        Message(role="assistant", content=final_response)
                    )
                    break

            response = final_response

        else:
            # User declined
            response = "Operation cancelled. No changes were made."
            reasoning_steps.append({
                "step": 1,
                "thought": "User declined the operation",
                "action": "cancel",
                "observation": "Operation cancelled",
            })
            session.cancel_confirmation()  # AWAITING_CONFIRMATION → IDLE
            session.add_message(Message(role="assistant", content=response))

        latency_ms = int((time.time() - start_time) * 1000)
        self.logger.log_interaction(
            session_id=session.session_id,
            user_query=user_message,
            reasoning_steps=reasoning_steps,
            tool_calls=tool_calls,
            final_response=response,
            latency_ms=latency_ms,
            llm_provider=str(self.llm),
        )

        return AgentResponse(
            session_id=session.session_id,
            response=response,
            reasoning_steps=reasoning_steps,
            tool_calls=tool_calls,
        )

    def _execute_confirmed_mutation(self, pending: PendingConfirmation) -> str:
        """
        Execute a confirmed mutation.

        Only reachable after session.begin_commit() has been called, which
        requires AWAITING_CONFIRMATION state — completing the state-machine
        guarantee that no write bypasses user confirmation.

        Enum extension: if the pending data contains `pending_enum_proposals`,
        the user has confirmed adding new enum values.  We extend the schema
        first, then execute the insert/update with allow_new_enum_values=True
        so the validator accepts the now-registered value.

        Range override: if the pending data contains `pending_range_proposals`,
        the user has confirmed they want to use an out-of-range value.  We
        proceed directly — type safety was already validated, only the range
        check was soft-failed.
        """
        try:
            data = pending.data
            op = pending.operation

            # Apply enum extensions if the user confirmed new property types
            enum_proposals = data.get("pending_enum_proposals", [])
            if enum_proposals:
                for proposal in enum_proposals:
                    extend_enum(
                        dataset=data["dataset"],
                        column=proposal["column"],
                        new_value=proposal["proposed_value"],
                    )

            # Range proposals don't need schema changes — just a note
            range_proposals = data.get("pending_range_proposals", [])

            # Build extension/override notes for the success message
            notes = []
            if enum_proposals:
                note_parts = ", ".join(
                    p["column"] + '="' + p["proposed_value"] + '"'
                    for p in enum_proposals
                )
                notes.append(f"Schema extended: {note_parts}")
            if range_proposals:
                note_parts = ", ".join(
                    f"{p['column']}={p['proposed_value']}"
                    for p in range_proposals
                )
                notes.append(f"Range override: {note_parts}")
            extended_note = f" ({'; '.join(notes)})" if notes else ""

            if op == "insert":
                result = self.dm.insert_rows(data["dataset"], data["rows"])
                return (
                    f"✅ Successfully inserted {result['inserted_count']} row(s). "
                    f"(Action ID: {result['action_id']} — use this to undo if needed)"
                    + extended_note
                )

            elif op == "update":
                result = self.dm.update_rows(
                    data["dataset"], data["filters"], data["updates"]
                )
                return (
                    f"✅ Successfully updated {result['updated_count']} row(s). "
                    f"(Action ID: {result['action_id']} — use this to undo if needed)"
                    + extended_note
                )

            elif op == "delete":
                result = self.dm.delete_rows(data["dataset"], data["filters"])
                return (
                    f"✅ Successfully deleted {result['deleted_count']} row(s). "
                    f"(Action ID: {result['action_id']} — use this to undo if needed)"
                )

            elif op == "undo":
                target_id = data.get("target_action_id")
                result = self.dm.undo(action_id=target_id)
                if "error" in result:
                    return f"❌ Undo failed: {result['error']}"
                return (
                    f"✅ Successfully undone {result['operation']} — "
                    f"{result['undone_count']} row(s) reverted."
                )

            elif op == "add_column":
                result = self.dm.add_column(
                    data["dataset"],
                    data["column_name"],
                    formula=data.get("formula"),
                    default_value=data.get("default_value"),
                )
                if "error" in result:
                    return f"❌ {result['error']}"
                return (
                    f"✅ Successfully added column '{result['column_name']}' "
                    f"to {result['total_rows']:,} rows. "
                    f"(Action ID: {result['action_id']} — use this to undo if needed)"
                )

            else:
                return f"❌ Unknown operation: {op}"

        except Exception as e:
            logger.error(
                "[%s] Mutation failed — op=%s, dataset=%s: %s",
                pending.operation,
                pending.operation,
                pending.data.get("dataset", "unknown"),
                e,
                exc_info=True,
            )
            return f"❌ Error executing {pending.operation}: {e}"