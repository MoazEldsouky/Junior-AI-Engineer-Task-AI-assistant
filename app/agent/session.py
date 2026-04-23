"""
Session Manager — manages conversation history and pending confirmations.

Each session tracks the full conversation history, any pending mutations
awaiting confirmation, and session metadata for cleanup.

State Machine
-------------
The session moves through exactly these states for any mutation:

    IDLE  →  AWAITING_CONFIRMATION  →  COMMITTING  →  IDLE

Key invariant: the system cannot reach COMMITTING without passing
through AWAITING_CONFIRMATION first.  The LLM has no way to skip it —
mutating tools (insert_data, update_data, delete_data, undo_change)
are structurally blocked when state == AWAITING_CONFIRMATION, so the
only execution path for the actual write is _execute_confirmed_mutation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class AgentState(Enum):
    """The current confirmation state of a session."""
    IDLE = "idle"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMMITTING = "committing"


# ---------------------------------------------------------------------------
# Message model
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single message in the conversation."""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    name: str | None = None  # Tool name for tool messages
    tool_calls: list[dict] | None = None  # For assistant messages with tool calls
    tool_call_id: str | None = None  # For tool response messages
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_llm_format(self) -> dict[str, Any]:
        """
        Convert to OpenAI-compatible message format.

        OpenAI-style APIs require specific formatting:
        - Assistant messages with tool_calls: content=null, tool_calls in
          {id, type:"function", function:{name, arguments}} format
        - Tool response messages: role="tool", tool_call_id, content as string
        """
        import json as _json

        if self.role == "assistant" and self.tool_calls:
            # Format tool calls for OpenAI-compatible APIs
            formatted_calls = []
            for tc in self.tool_calls:
                args = tc.get("arguments", {})
                if not isinstance(args, str):
                    args = _json.dumps(args, default=str)
                formatted_calls.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": args,
                    },
                })
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": formatted_calls,
            }

        if self.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id or "",
                "content": self.content or "",
            }

        return {"role": self.role, "content": self.content or ""}


# ---------------------------------------------------------------------------
# Pending confirmation
# ---------------------------------------------------------------------------

@dataclass
class PendingConfirmation:
    """Tracks a mutation awaiting user confirmation."""
    operation: str  # "insert", "update", "delete", "undo"
    dataset: str
    data: dict[str, Any]  # Full tool result data for executing the mutation
    preview: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

# Tool names that are allowed to execute only via the confirmation path,
# not directly from the LLM tool-call loop.
MUTATING_TOOLS: frozenset[str] = frozenset({
    "insert_data",
    "update_data",
    "delete_data",
    "undo_change",
})


@dataclass
class Session:
    """A conversation session with history and state."""
    session_id: str
    history: list[Message] = field(default_factory=list)
    state: AgentState = AgentState.IDLE
    pending_confirmation: PendingConfirmation | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_active: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def is_awaiting_confirmation(self) -> bool:
        return self.state == AgentState.AWAITING_CONFIRMATION

    def request_confirmation(self, pending: PendingConfirmation) -> None:
        """Transition IDLE → AWAITING_CONFIRMATION and store pending op."""
        self.pending_confirmation = pending
        self.state = AgentState.AWAITING_CONFIRMATION

    def begin_commit(self) -> None:
        """Transition AWAITING_CONFIRMATION → COMMITTING (the only path)."""
        if self.state != AgentState.AWAITING_CONFIRMATION:
            raise RuntimeError(
                f"Cannot commit: session state is {self.state.value!r}, "
                "expected 'awaiting_confirmation'. "
                "COMMITTING is only reachable via AWAITING_CONFIRMATION."
            )
        self.state = AgentState.COMMITTING

    def finish_commit(self) -> None:
        """Transition COMMITTING → IDLE and clear pending confirmation."""
        self.pending_confirmation = None
        self.state = AgentState.IDLE

    def cancel_confirmation(self) -> None:
        """Transition AWAITING_CONFIRMATION → IDLE without committing."""
        self.pending_confirmation = None
        self.state = AgentState.IDLE

    def is_tool_blocked(self, tool_name: str) -> bool:
        """
        Return True if a tool call must be blocked.

        Mutating tools are blocked while in AWAITING_CONFIRMATION so that
        a hallucinating LLM cannot bypass the user confirmation step.
        """
        return self.is_awaiting_confirmation and tool_name in MUTATING_TOOLS

    # ------------------------------------------------------------------
    # History helpers
    # ------------------------------------------------------------------

    def add_message(self, message: Message):
        """Add a message to the conversation history."""
        self.history.append(message)
        self.last_active = datetime.utcnow().isoformat() + "Z"

    def get_llm_messages(self, max_messages: int = 20) -> list[dict]:
        """Get conversation history in LLM format, trimmed to max length."""
        # Always keep the system message (first) + last N messages
        messages = []
        for msg in self.history:
            if msg.role == "system":
                messages.append(msg.to_llm_format())
                break

        # Get recent messages (non-system)
        non_system = [m for m in self.history if m.role != "system"]
        recent = non_system[-max_messages:] if len(non_system) > max_messages else non_system

        for msg in recent:
            messages.append(msg.to_llm_format())

        return messages


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class SessionManager:
    """Manages multiple conversation sessions."""

    def __init__(self, ttl_minutes: int = 60):
        self._sessions: dict[str, Session] = {}
        self._ttl = timedelta(minutes=ttl_minutes)

    def create_session(self) -> Session:
        """Create a new session."""
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get an existing session by ID."""
        return self._sessions.get(session_id)

    def get_or_create_session(self, session_id: str | None = None) -> Session:
        """Get an existing session or create a new one."""
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.last_active = datetime.utcnow().isoformat() + "Z"
            return session
        return self.create_session()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def get_history(self, session_id: str) -> list[dict] | None:
        """Get conversation history for a session."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp,
            }
            for msg in session.history
            if msg.role in ("user", "assistant")
        ]

    def cleanup_stale(self):
        """Remove sessions that haven't been active within the TTL."""
        now = datetime.utcnow()
        stale = []
        for sid, session in self._sessions.items():
            last = datetime.fromisoformat(session.last_active.replace("Z", ""))
            if now - last > self._ttl:
                stale.append(sid)
        for sid in stale:
            del self._sessions[sid]
