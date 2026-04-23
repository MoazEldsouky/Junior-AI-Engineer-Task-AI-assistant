# 🤖 AI Agent Excel Assistant

An AI-powered assistant that lets you interact with Excel data using **natural language**. Ask questions, update records, insert rows, delete data, and undo changes — all through a simple chat interface.

Built from scratch with **no agent frameworks** (no LangChain, LlamaIndex, etc.) — just clean Python, a custom tool-based architecture, and free LLM APIs.

---

## ✨ Features

| Feature | Description | 
|-------------------|---------------|
| **Natural Language Queries**              | Ask anything about your data in plain English                                            |
| **CRUD Operations**                         | Insert, update, and delete rows via chat                                                 |
| **Preview + Confirmation**                  | Every mutation shows a before/after preview and requires explicit confirmation           |
| **Structurally Enforced Confirmation**      | State machine (`IDLE → AWAITING_CONFIRMATION → COMMITTING`) — the LLM cannot bypass it  |
| **Dynamic Schema & Range Overrides**        | Add new property types or bypass range limits with user confirmation                     |
| **Undo System**                             | Full mutation log with undo support for any past change                                  |
| **Post-Mutation Display**                   | Automatically queries and renders affected rows in Markdown tables after mutations       |
| **Data Validation**                         | Type checking, range constraints, and enum validation before mutations                   |
| **Multi-Turn Conversations**                | Session memory for follow-up questions                                                   |
| **Multiple LLM Providers**                  | Gemini, Groq, OpenRouter, GitHub Models — switch freely                                  |
| **Structured Logging**                      | Every interaction logged as JSON for full traceability                                   |
| **REST API**                              | Clean FastAPI endpoints with auto-generated docs                                       |

---

## 📊 Datasets

The assistant works with two Excel datasets:

**Real Estate Listings** (1,000 rows)

- Listing ID, Property Type, City, State, Bedrooms, Bathrooms, Square Footage, Year Built, List Price, Sale Price, Listing Status

**Marketing Campaigns** (1,000 rows)

- Campaign ID, Campaign Name, Channel, Start Date, End Date, Budget Allocated, Amount Spent, Impressions, Clicks, Conversions, Revenue Generated

---

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/Junior-AI-Engineer-Task-AI-assistant.git
cd Junior-AI-Engineer-Task-AI-assistant
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API Keys

Copy the example env file and add at least one LLM API key:

```bash
cp .env.example .env
```

Edit `.env` and add your API key(s):

```env
# Pick one (all are free):
GEMINI_API_KEY=your-key      # https://aistudio.google.com/apikey
GROQ_API_KEY=your-key        # https://console.groq.com/keys
OPENROUTER_API_KEY=your-key  # https://openrouter.ai/keys
GITHUB_TOKEN=your-pat        # https://github.com/settings/tokens

# Set active provider
LLM_PROVIDER=GITHUB_TOKEN
LLM_MODEL=gpt-4o
```

### 4. Start the Server

```bash
uvicorn app.main:app --reload
```

The server will start at `http://localhost:8000`. API docs are available at `http://localhost:8000/docs`.

---

## 💬 Usage Examples

### Query Data

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How many listings are in California?"}'
```

**Response:**

```json
{
  "session_id": "abc-123",
  "response": "There are 88 listings in California.",
  "reasoning_steps": [{"step": 1, "action": "query_data", ...}],
  "requires_confirmation": false
}
```

### Complex Aggregation

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the average sale price of houses in Washington?"}'
```

### Update Data (Preview + Confirm)

**Step 1: Request the update**

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Update the list price of LST-5001 to 482000"}'
```

**Response includes a preview:**

```text
📝 About to update 1 row(s):

📊 CONFIRMATION: STAGED EXCEL UPDATE (1 Row)
──────────────────────────────────────────────────
📁 File: Real Estate Listings.xlsx

Before → After:
  Record 'LST-5001':
    List Price: 351000 → 482000

Proceed with update? (yes/no)
```

**Step 2: Confirm**

```bash
curl -X POST http://localhost:8000/chat/confirm \
  -H "Content-Type: application/json" \
  -d '{"session_id": "abc-123", "confirmed": true}'
```

**Response:**
```json
{
  "session_id": "abc-123",
  "response": "✅ Successfully updated 1 row(s). (Action ID: act_12345)\n\n| ID | List Price | ...\n|---|---|...",
  "reasoning_steps": [{"step": 2, "action": "query_data", ...}],
  "requires_confirmation": false
}
```

### Undo a Change

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Undo the last change"}'
```

### Multi-Turn Conversation

```bash
# First question
curl -X POST http://localhost:8000/chat \
  -d '{"message": "How many houses are in Washington?"}'
# Returns session_id: "abc-123"

# Follow-up (reuse session_id)
curl -X POST http://localhost:8000/chat \
  -d '{"message": "And how about California?", "session_id": "abc-123"}'
```

---

## 📡 API Reference

| Method     | Endpoint                   | Description                             |
| ---------- | -------------------------- | --------------------------------------- |
| `POST`   | `/chat`                  | Send a message to the agent             |
| `POST`   | `/chat/confirm`          | Confirm or cancel a pending mutation    |
| `GET`    | `/sessions/{id}/history` | Get conversation history                |
| `DELETE` | `/sessions/{id}`         | Delete a session                        |
| `GET`    | `/health`                | System status and configuration         |
| `GET`    | `/datasets`              | List datasets with schemas              |
| `GET`    | `/docs`                  | Interactive API documentation (Swagger) |

---

## 🔧 Tools

All capabilities are implemented as custom tools that the agent dynamically selects:

| Tool               | Description                                    |
| ------------------ | ---------------------------------------------- |
| `query_data`     | Filter, sort, aggregate, and search data       |
| `insert_data`    | Add new rows with validation                   |
| `update_data`    | Modify existing rows with before/after preview |
| `delete_data`    | Remove rows with preview                       |
| `inspect_schema` | Describe datasets, columns, and data types     |
| `undo_change`    | Revert any previous mutation                   |
| `list_changes`   | Show mutation history with action IDs          |

---

## 🏗️ Architecture

```
User → FastAPI → Session Manager → Agent (ReAct Loop) → LLM Provider
                                        ↕
                                   Tool Registry
                                        ↕
                              Data Manager (pandas)
                                        ↕
                                   Excel Files
```

**ReAct Loop**: The agent follows a Reason → Act → Observe cycle:

1. Receives user query + conversation history
2. LLM reasons about what to do → selects a tool
3. Agent executes the tool → feeds result back to LLM
4. LLM generates the final human-readable answer

**LLM Abstraction**: All providers implement the same interface. The agent never knows which LLM is active — you can switch providers by changing one env variable.

**Confirmation State Machine**: Every mutation passes through a structurally enforced state machine:

```
IDLE → AWAITING_CONFIRMATION → COMMITTING → IDLE
```

Mutating tools (`insert_data`, `update_data`, `delete_data`, `undo_change`) are blocked at the code level while the session is in `AWAITING_CONFIRMATION`. The LLM cannot bypass this — even if it hallucinates a direct tool call.

**LLM Provider Hierarchy**: Providers follow a clean inheritance model:

```
BaseLLMProvider (abstract)
├── GeminiProvider             ← Google GenAI SDK
└── OpenAICompatibleProvider   ← shared generate/parse
    ├── GroqProvider
    ├── GitHubModelsProvider
    └── OpenRouterProvider
```

All OpenAI-compatible providers share a single `generate()` + `_parse_openai_response()` implementation. Each subclass declares only its API URL and optional headers.

---

## 📁 Project Structure

```
├── app/
│   ├── main.py              # FastAPI server + endpoints
│   ├── config.py            # Settings from .env
│   ├── agent/
│   │   ├── core.py          # ReAct reasoning loop + state-machine confirmation
│   │   ├── prompt.py        # System prompts
│   │   └── session.py       # Session, AgentState enum, & conversation memory
│   ├── llm/
│   │   ├── base.py          # BaseLLMProvider + OpenAICompatibleProvider
│   │   ├── gemini.py        # Google Gemini (separate SDK)
│   │   ├── groq.py          # Groq (thin subclass)
│   │   ├── openrouter.py    # OpenRouter (thin subclass)
│   │   └── github_models.py # GitHub Models (thin subclass)
│   ├── tools/
│   │   ├── base.py          # BaseTool + ToolRegistry
│   │   ├── query.py         # QueryTool
│   │   ├── insert.py        # InsertTool (with enum extension support)
│   │   ├── update.py        # UpdateTool (with enum extension support)
│   │   ├── delete.py        # DeleteTool
│   │   ├── schema_inspect.py # SchemaInspectTool
│   │   ├── undo.py          # UndoTool
│   │   └── list_changes.py  # ListChangesTool
│   ├── data/
│   │   ├── manager.py       # DataManager (load/save/query)
│   │   └── validator.py     # Validation engine + EnumProposal
│   └── logging/
│       └── logger.py        # Structured JSON logger
├── frontend/                # Vanilla JS chat UI
├── data/                    # Runtime data (write-log)
├── logs/                    # Interaction logs
├── notebooks/
│   └── experimentation.ipynb
├── .env.example
├── requirements.txt
├── README.md
├── DECISIONS.md
└── Task.txt
```

---

## ⚙️ Configuration

All settings are in `.env`:

| Variable               | Default    | Description                                 |
| ---------------------- | ---------- | ------------------------------------------- |
| `LLM_PROVIDER`       | `gemini` | Active LLM provider                         |
| `LLM_MODEL`          | (auto)     | Model name (sensible defaults per provider) |
| `GEMINI_API_KEY`     |            | Google Gemini API key                       |
| `GROQ_API_KEY`       |            | Groq API key                                |
| `OPENROUTER_API_KEY` |            | OpenRouter API key                          |
| `GITHUB_TOKEN`       |            | GitHub PAT for Azure models                 |

**Supported LLM Providers:**

| Provider          | Default Model                        | Best For                   |
| ----------------- | ------------------------------------ | -------------------------- |
| `gemini`        | `gemini-2.0-flash`                 | Best free function calling |
| `groq`          | `llama-3.3-70b-versatile`          | Fastest inference          |
| `openrouter`    | `google/gemini-2.0-flash-exp:free` | Multi-model access         |
| `github_models` | `gpt-4o`                           | GPT-4o free access         |

---

## 📓 Experimentation Notebook

See [`notebooks/experimentation.ipynb`](notebooks/experimentation.ipynb) for a full walkthrough:

- Loading and exploring data
- Defining and testing individual tools
- Building the agent
- Testing various scenarios end-to-end
