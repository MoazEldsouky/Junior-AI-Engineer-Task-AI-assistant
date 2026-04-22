# AI Agent Excel Assistant API Documentation

This document describes the REST API endpoints available in the AI Agent Excel Assistant, built with FastAPI.

## Base URL

By default, the API is served at `http://localhost:8000`. If you have configured a different host or port, adjust the base URL accordingly.

---

## 1. Chat Endpoints

### 1.1 `POST /chat`

Send a natural language message to the AI agent. The agent will process the request, interact with the data, and return a response. If the action involves data modification, it will require confirmation.

**Request Body** (`application/json`):

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `message` | `string` | **Yes** | The user's natural language message. |
| `session_id` | `string` | No | Session ID for multi-turn conversations. Omit to start a new session. |

**Example Request:**
```json
{
  "message": "Show me all houses in San Francisco under $1,000,000",
  "session_id": "optional-session-id-here"
}
```

**Response Body** (`application/json`):

| Field | Type | Description |
| :--- | :--- | :--- |
| `session_id` | `string` | The active session ID (useful if one was generated). |
| `response` | `string` | The agent's conversational response. |
| `reasoning_trace` | `string` | A LangChain-style formatted trace of the agent's reasoning. |
| `reasoning_steps` | `array of objects` | The raw steps the agent took to reach the response. |
| `tool_calls` | `array of objects` | Any specific tools that the agent executed. |
| `requires_confirmation` | `boolean` | `true` if a pending data mutation needs confirmation. |
| `confirmation_preview` | `string` or `null` | A formatted preview of the changes pending confirmation. |

---

### 1.2 `POST /chat/confirm`

Confirm or cancel a pending data mutation (e.g., an insert, update, or delete). This endpoint should be used when a `/chat` response returns `requires_confirmation: true`.

**Request Body** (`application/json`):

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `session_id` | `string` | **Yes** | The session ID that has a pending confirmation. |
| `confirmed` | `boolean` | **Yes** | `true` to proceed with the mutation, `false` to cancel it. |

**Example Request:**
```json
{
  "session_id": "active-session-id",
  "confirmed": true
}
```

**Response Body** (`application/json`): Returns the same `ChatResponse` model as the `/chat` endpoint.

---

## 2. Session Management

### 2.1 `GET /sessions/{session_id}/history`

Retrieve the conversation history for a specific session.

**Path Parameters:**
- `session_id` (string): The ID of the session.

**Response Body** (`application/json`):

| Field | Type | Description |
| :--- | :--- | :--- |
| `session_id` | `string` | The ID of the requested session. |
| `history` | `array of objects` | The list of messages in the session history. |

---

### 2.2 `DELETE /sessions/{session_id}`

Delete an active conversation session and clear its history from memory.

**Path Parameters:**
- `session_id` (string): The ID of the session to delete.

**Response Body** (`application/json`):
```json
{
  "message": "Session {session_id} deleted"
}
```

---

## 3. System & Data Inspection

### 3.1 `GET /health`

Health check endpoint to verify the system status, active LLM configuration, and loaded datasets.

**Response Body** (`application/json`):

| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | Typically `"healthy"` if the system is running. |
| `llm_provider` | `string` | The name of the configured LLM provider (e.g., `"gemini"`, `"groq"`). |
| `llm_model` | `string` | The specific model name being used. |
| `datasets` | `array of objects` | A brief list/summary of currently loaded datasets. |

---

### 3.2 `GET /datasets`

List all available datasets along with their complete schemas (columns, data types, constraints, etc.).

**Response Body** (`application/json`):

| Field | Type | Description |
| :--- | :--- | :--- |
| `datasets` | `array of objects` | Comprehensive list of datasets and their schemas. |
