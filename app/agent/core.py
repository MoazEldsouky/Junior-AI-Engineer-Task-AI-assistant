"""
Agent Core — the ReAct reasoning loop.

Orchestrates the interaction between the user, the LLM, and the tools.
Follows a Reason → Act → Observe loop with explicit reasoning traces.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import BaseLLMProvider, LLMResponse, get_provider
from app.tools.base import ToolRegistry, ToolResult
from app.agent.session import Session, Message, PendingConfirmation
from app.agent.prompt import build_system_prompt
from app.data.manager import DataManager
from app.logging.logger import InteractionLogger


# Maximum rows to include in tool observation messages sent to the LLM.
# Keeps token count manageable while still giving the model enough data
# to compose an accurate response.
MAX_OBSERVATION_ROWS = 10


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
    7. If tool requires confirmation → pause and return preview
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

        # Handle confirmation responses
        if session.pending_confirmation:
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

                    # Check if tool requires confirmation
                    if result.requires_confirmation:
                        # Store pending confirmation
                        op = result.data.get("operation", "unknown") if result.data else "unknown"
                        ds = result.data.get("dataset", "") if result.data else ""
                        session.pending_confirmation = PendingConfirmation(
                            operation=op,
                            dataset=ds,
                            data=result.data if result.data else {},
                            preview=result.preview or result.message,
                        )
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
                        break  # Stop loop — wait for confirmation

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

            if len(rows) <= MAX_OBSERVATION_ROWS:
                # Small result set — include everything
                return json.dumps(data, default=str)

            # Large result set — truncate and summarize
            compact = {
                "total_matching": total,
                "rows_returned": len(rows),
                "showing_first": MAX_OBSERVATION_ROWS,
                "data": rows[:MAX_OBSERVATION_ROWS],
                "note": f"Showing first {MAX_OBSERVATION_ROWS} of {total} matching rows. Use filters, sort, or limit to narrow results.",
            }
            return json.dumps(compact, default=str)

        # For aggregation results, schema inspections, etc. — pass through
        return json.dumps(data, default=str)

    async def _handle_confirmation(
        self, session: Session, user_message: str, start_time: float
    ) -> AgentResponse:
        """Handle a user's yes/no response to a pending confirmation."""
        pending = session.pending_confirmation
        session.add_message(Message(role="user", content=user_message))

        normalized = user_message.strip().lower()
        confirmed = normalized in ("yes", "y", "confirm", "proceed", "ok", "sure", "do it", "go ahead")
        declined = normalized in ("no", "n", "cancel", "abort", "stop", "nevermind", "never mind")

        reasoning_steps = []
        tool_calls = []

        if not confirmed and not declined:
            # Ambiguous — ask the LLM to interpret
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
                    confirmation_preview=pending.preview,
                )

        if confirmed:
            # Execute the mutation
            result = self._execute_confirmed_mutation(pending)
            reasoning_steps.append({
                "step": 1,
                "thought": "User confirmed the operation",
                "action": f"execute_{pending.operation}",
                "observation": result,
            })
            response = result
            session.pending_confirmation = None
        else:
            response = "Operation cancelled. No changes were made."
            reasoning_steps.append({
                "step": 1,
                "thought": "User declined the operation",
                "action": "cancel",
                "observation": "Operation cancelled",
            })
            session.pending_confirmation = None

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
        """Execute a confirmed mutation."""
        try:
            data = pending.data
            op = pending.operation

            if op == "insert":
                result = self.dm.insert_rows(data["dataset"], data["rows"])
                return (
                    f"✅ Successfully inserted {result['inserted_count']} row(s). "
                    f"(Action ID: {result['action_id']} — use this to undo if needed)"
                )

            elif op == "update":
                result = self.dm.update_rows(
                    data["dataset"], data["filters"], data["updates"]
                )
                return (
                    f"✅ Successfully updated {result['updated_count']} row(s). "
                    f"(Action ID: {result['action_id']} — use this to undo if needed)"
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

            else:
                return f"❌ Unknown operation: {op}"

        except Exception as e:
            return f"❌ Error executing {pending.operation}: {e}"
