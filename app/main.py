"""
FastAPI Application — main entry point for the AI Agent Excel Assistant.

Defines all API endpoints for chatting with the agent, confirming mutations,
managing sessions, and inspecting datasets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.data.manager import DataManager
from app.data.validator import Validator
from app.tools.base import ToolRegistry
from app.tools.query import QueryTool
from app.tools.insert import InsertTool
from app.tools.update import UpdateTool
from app.tools.delete import DeleteTool
from app.tools.schema_inspect import SchemaInspectTool
from app.tools.undo import UndoTool
from app.tools.list_changes import ListChangesTool
from app.tools.add_column import AddColumnTool
from app.llm.base import get_provider
from app.agent.core import Agent
from app.agent.session import SessionManager
from app.logging.logger import InteractionLogger


logger = logging.getLogger("api")


# ---------------------------------------------------------------------------
# Global state (initialized in lifespan)
# ---------------------------------------------------------------------------
data_manager: DataManager | None = None
agent: Agent | None = None
session_manager: SessionManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all components on startup."""
    global data_manager, agent, session_manager

    logger.info("🚀 Starting AI Agent Excel Assistant...")

    # 1. Data Manager
    data_manager = DataManager()
    logger.info(f"📊 Loaded {len(data_manager.list_datasets())} datasets")

    # 2. Validator
    validator = Validator()

    # 3. Tool Registry
    registry = ToolRegistry()
    registry.register(QueryTool(data_manager))
    registry.register(InsertTool(data_manager, validator))
    registry.register(UpdateTool(data_manager, validator))
    registry.register(DeleteTool(data_manager))
    registry.register(SchemaInspectTool(data_manager))
    registry.register(UndoTool(data_manager))
    registry.register(ListChangesTool(data_manager))
    registry.register(AddColumnTool(data_manager))
    logger.info(f"🔧 Registered {len(registry.list_names())} tools: {registry.list_names()}")

    # 4. LLM Provider
    llm = get_provider(
        provider_name=settings.llm_provider.value,
        api_key=settings.active_api_key,
        model=settings.active_model,
    )
    logger.info(f"🤖 LLM: {llm}")

    # 5. Logger
    interaction_logger = InteractionLogger()

    # 6. Agent
    agent = Agent(
        llm=llm,
        tool_registry=registry,
        data_manager=data_manager,
        logger=interaction_logger,
        max_iterations=settings.max_agent_iterations,
        max_history=settings.max_history_messages,
    )

    # 7. Session Manager
    session_manager = SessionManager(ttl_minutes=settings.session_ttl_minutes)

    logger.info("✅ System ready!")
    yield
    logger.info("👋 Shutting down...")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Agent Excel Assistant",
    description=(
        "An AI-powered assistant that lets you interact with Excel data "
        "using natural language. Query, insert, update, delete, and undo "
        "operations on Real Estate Listings and Marketing Campaigns datasets."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Request body for the /chat endpoint."""
    message: str = Field(..., description="The user's natural language message")
    session_id: str | None = Field(None, description="Session ID for multi-turn conversations. Omit to start a new session.")


class ChatResponse(BaseModel):
    """Response from the /chat endpoint."""
    session_id: str
    response: str
    reasoning_trace: str = ""
    reasoning_steps: list[dict] = []
    tool_calls: list[dict] = []
    requires_confirmation: bool = False
    confirmation_preview: str | None = None


class ConfirmRequest(BaseModel):
    """Request body for the /chat/confirm endpoint."""
    session_id: str = Field(..., description="Session ID with a pending confirmation")
    confirmed: bool = Field(..., description="True to proceed, False to cancel")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    llm_provider: str
    llm_model: str
    datasets: list[dict]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_reasoning_trace(steps: list[dict]) -> str:
    """
    Format reasoning steps as a LangChain-style agent trace.

    Example output:
        > Entering Agent Pipeline...

        THOUGHT: Locating rows matching the user's criteria.
        ACTION: query_data({"dataset": "real_estate_listings", ...})
        OBSERVATION: 3 rows retrieved.

        THOUGHT: Data gathered. Composing final answer for the user.

        > Finished Agent Pipeline.
    """
    if not steps:
        return ""

    lines = ["> Entering Agent Pipeline...", ""]

    for step in steps:
        step_type = step.get("type", "action")
        thought = step.get("thought", "")
        action = step.get("action")
        observation = step.get("observation", "")

        if thought:
            lines.append(f"THOUGHT: {thought}")

        if action:
            lines.append(f"ACTION: {action}")

        if observation:
            # Truncate long observations for readability
            obs_display = observation if len(observation) <= 300 else observation[:300] + "..."
            lines.append(f"OBSERVATION: {obs_display}")

        lines.append("")

    lines.append("> Finished Agent Pipeline.")
    return "\n".join(lines)


def _sse_event(event: str, data: Any) -> str:
    """Format a Server-Sent Event."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a natural language message to the AI agent.

    The agent will reason about your request, use appropriate tools to
    query or modify data, and return a clear response. If the request
    involves a data mutation, the agent will return a preview and ask
    for confirmation.
    """
    session = session_manager.get_or_create_session(request.session_id)

    result = await agent.process_message(session, request.message)

    # Build LangChain-style reasoning trace
    trace = _format_reasoning_trace(result.reasoning_steps)

    return ChatResponse(
        session_id=result.session_id,
        response=result.response,
        reasoning_trace=trace,
        reasoning_steps=result.reasoning_steps,
        tool_calls=result.tool_calls,
        requires_confirmation=result.requires_confirmation,
        confirmation_preview=result.confirmation_preview,
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Streaming version of /chat — sends SSE events for reasoning steps
    and then streams the final response text chunk-by-chunk.

    Events:
        session_id   — {session_id}
        thinking     — {step, thought, action, observation}  (one per reasoning step)
        thinking_end — {}
        token        — {token}  (chunks of the final response)
        done         — {latency_ms, requires_confirmation, confirmation_preview}
        error        — {message}
    """
    async def event_generator():
        start_time = time.time()

        try:
            session = session_manager.get_or_create_session(request.session_id)
            yield _sse_event("session_id", {"session_id": session.session_id})
            await asyncio.sleep(settings.sse_event_delay)

            result = await agent.process_message(session, request.message)

            # Stream reasoning steps
            for step in result.reasoning_steps:
                yield _sse_event("thinking", step)
                await asyncio.sleep(settings.sse_thinking_delay)

            yield _sse_event("thinking_end", {})
            await asyncio.sleep(settings.sse_event_delay)

            # Stream the final response in chunks for a typing effect
            response_text = result.response or ""
            chunk_size = settings.sse_chunk_size
            for i in range(0, len(response_text), chunk_size):
                chunk = response_text[i : i + chunk_size]
                yield _sse_event("token", {"token": chunk})
                await asyncio.sleep(settings.sse_token_delay)

            latency_ms = int((time.time() - start_time) * 1000)

            yield _sse_event("done", {
                "latency_ms": latency_ms,
                "requires_confirmation": result.requires_confirmation,
                "confirmation_preview": result.confirmation_preview,
                "reasoning_trace": _format_reasoning_trace(result.reasoning_steps),
            })

        except Exception as e:
            logger.exception("Streaming error")
            yield _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat/confirm", response_model=ChatResponse)
async def confirm(request: ConfirmRequest):
    """
    Confirm or cancel a pending data mutation.

    After the agent returns a mutation preview with requires_confirmation=true,
    use this endpoint to either proceed (confirmed=true) or cancel (confirmed=false).
    """
    session = session_manager.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.pending_confirmation:
        raise HTTPException(status_code=400, detail="No pending confirmation for this session")

    message = "yes" if request.confirmed else "no"
    result = await agent.process_message(session, message)

    trace = _format_reasoning_trace(result.reasoning_steps)

    return ChatResponse(
        session_id=result.session_id,
        response=result.response,
        reasoning_trace=trace,
        reasoning_steps=result.reasoning_steps,
        tool_calls=result.tool_calls,
        requires_confirmation=result.requires_confirmation,
        confirmation_preview=result.confirmation_preview,
    )


@app.get("/sessions/{session_id}/history")
async def get_history(session_id: str):
    """Get the conversation history for a session."""
    history = session_manager.get_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "history": history}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a conversation session."""
    if session_manager.delete_session(session_id):
        return {"message": f"Session {session_id} deleted"}
    raise HTTPException(status_code=404, detail="Session not found")


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check — shows system status, active LLM, and loaded datasets."""
    return HealthResponse(
        status="healthy",
        llm_provider=settings.llm_provider.value,
        llm_model=settings.active_model,
        datasets=data_manager.list_datasets() if data_manager else [],
    )


@app.get("/datasets")
async def list_datasets():
    """List all available datasets with their schemas."""
    if not data_manager:
        raise HTTPException(status_code=503, detail="System not initialized")
    datasets = []
    for ds in data_manager.list_datasets():
        schema = data_manager.get_schema(ds["key"])
        datasets.append(schema)
    return {"datasets": datasets}
