"""
Session Manager — manages conversation history and pending confirmations.

Each session tracks the full conversation history, any pending mutations
awaiting confirmation, and session metadata for cleanup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


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



@dataclass
class PendingConfirmation:
    """Tracks a mutation awaiting user confirmation."""
    operation: str  # "insert", "update", "delete", "undo"
    dataset: str
    data: dict[str, Any]  # Full tool result data for executing the mutation
    preview: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


@dataclass
class Session:
    """A conversation session with history and state."""
    session_id: str
    history: list[Message] = field(default_factory=list)
    pending_confirmation: PendingConfirmation | None = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_active: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

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
