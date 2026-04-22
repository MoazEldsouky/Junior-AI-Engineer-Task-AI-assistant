"""
Structured Interaction Logger — logs every interaction as JSON.

Each interaction is stored as a separate JSON file in the logs/ directory
for full traceability and easy debugging.
"""

from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import LOGS_DIR


# Also set up standard Python logging for console output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


class InteractionLogger:
    """Logs each agent interaction as a structured JSON file."""

    def __init__(self, log_dir: Path = LOGS_DIR):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.console_logger = logging.getLogger("agent")

    def log_interaction(
        self,
        session_id: str,
        user_query: str,
        reasoning_steps: list[dict],
        tool_calls: list[dict],
        final_response: str,
        latency_ms: int,
        llm_provider: str = "",
        error: str | None = None,
    ):
        """Log a complete interaction to a JSON file."""
        interaction_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"

        log_entry = {
            "interaction_id": interaction_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "user_query": user_query,
            "reasoning_steps": reasoning_steps,
            "tool_decisions": [
                {"tool": tc.get("tool"), "reason": tc.get("input")}
                for tc in tool_calls
            ],
            "tool_inputs": [
                {"tool": tc.get("tool"), "input": tc.get("input")}
                for tc in tool_calls
            ],
            "tool_outputs": [
                {"tool": tc.get("tool"), "output": tc.get("output"), "success": tc.get("success")}
                for tc in tool_calls
            ],
            "final_response": final_response,
            "llm_provider": llm_provider,
            "latency_ms": latency_ms,
            "error": error,
        }

        # Write to file
        filename = f"{timestamp.replace(':', '-').replace('.', '-')}_{interaction_id[:8]}.json"
        filepath = self.log_dir / filename
        with open(filepath, "w") as f:
            json.dump(log_entry, f, indent=2, default=str)

        # Console log
        tools_used = ", ".join(tc.get("tool", "?") for tc in tool_calls) or "none"
        self.console_logger.info(
            f"[{session_id[:8]}] Query: {user_query[:80]}... | "
            f"Tools: {tools_used} | Latency: {latency_ms}ms"
        )
