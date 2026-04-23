# DECISIONS.md — Design Choices, Tradeoffs, and Potential Improvements

This document explains the key design decisions made in building the AI Agent Excel Assistant, the tradeoffs involved, and what I would do differently with more time.

---

## 1. Architecture: ReAct Loop

**Decision:** Use a ReAct (Reason + Act) pattern where the LLM reasons step-by-step, selects tools, observes results, and iterates until it has enough information to answer.

**Why:** ReAct is the simplest pattern that gives us structured reasoning with tool use. It's transparent (every step is visible), debuggable (you can see what the agent was thinking), and works well with function-calling LLMs. More complex patterns (tree of thought, plan-then-execute) add latency and complexity without clear benefit for this use case.

**Tradeoff:** The agent may take multiple iterations for complex queries, increasing latency. A max iteration limit (10) prevents infinite loops.

---

## 2. Tool Granularity: 8 Specialized Tools

**Decision:** Create 8 focused tools rather than one generic "execute SQL" tool.

| Tool | Purpose |
|------|---------|
| `query_data` | Read-only queries with filters + aggregation |
| `insert_data` | Add rows with validation |
| `update_data` | Modify rows with before/after preview |
| `delete_data` | Remove rows with preview |
| `inspect_schema` | Describe available data |
| `undo_change` | Revert mutations |
| `list_changes` | Review mutation history |
| `add_column` | Add a new column computed via formula |

**Why:** Specialized tools with clear parameter schemas guide the LLM to make better decisions. A generic tool would require the LLM to generate complex query syntax, increasing error rates. Each tool has explicit validation, preview logic, and safety checks that wouldn't fit cleanly in a single tool.

**Tradeoff:** More tools means a larger function-calling schema for the LLM to process. For free-tier models with limited context, this adds token overhead. In practice, 8 tools is well within the capacity of models like Gemini Flash and Llama 3.3.

---

## 3. LLM Abstraction: Provider-Agnostic Interface

**Decision:** Abstract all LLM providers behind a single `BaseLLMProvider` interface with a factory function.

**Why:** The task requires supporting 4+ LLM providers. By normalizing everything to OpenAI-style messages + tool schemas, the agent code is completely decoupled from the provider. Switching from Groq to Gemini is a one-line `.env` change.

**Implementation Details:**
- OpenAI-compatible APIs (Groq, OpenRouter, GitHub Models) share request format, response structure, and parsing logic — all consolidated into a single `OpenAICompatibleProvider` base class (see §15)
- Gemini uses a completely different SDK (Google Generative AI) and remains its own separate `GeminiProvider` implementation
- All responses normalize to `LLMResponse(content, tool_calls, usage)`

**Tradeoff:** Some Gemini-specific features (like grounding, multi-modal) aren't accessible through this abstraction. For this task, text + function calling is all we need.

---

## 4. Data Layer: pandas + In-Memory Cache

**Decision:** Load Excel files into pandas DataFrames on startup, operate on them in memory, and write back to Excel after mutations.

**Why:**
- **Fast reads:** DataFrame operations are sub-millisecond vs. repeated disk reads
- **Rich querying:** pandas gives us filtering, aggregation, sorting, and grouping for free
- **Simple persistence:** Write-through to Excel preserves the original format

**Tradeoff:**
- **Memory usage:** Both datasets (~2,000 rows) fit easily in memory. This wouldn't scale to millions of rows.
- **Concurrent writes:** A simple threading lock prevents race conditions, but wouldn't scale to high-concurrency scenarios.
- **No transactions:** If the process crashes mid-write, data could be inconsistent. For a single-user tool, this is acceptable.

**What I'd change with more time:** Use SQLite as an intermediate cache for better concurrency and crash recovery, while still exporting to Excel on demand.

---

## 5. Validation: Fail-Fast with Clear Errors, Enum Extensions & Range Overrides

**Decision:** Every mutation passes through a `Validator` that checks types, ranges, enums, and required fields before any data is touched. Unknown enum values and out-of-range values now trigger a **confirmation-based extension/override flow** rather than a hard rejection (see §17).

**Why:** Catching errors before they reach the database prevents corrupted data and gives users actionable feedback. The validator knows the schema constraints (e.g., Property Type must be House/Condo/Apartment/Townhouse) and returns specific error messages.

**Tradeoff:**
* **Strict vs. Flexible Schemas:** Users can now add new enum values (e.g., "Twitter" as a Channel) or bypass numeric range soft limits through a two-step confirmation flow that ensures they understand they are permanently extending the schema or overriding a rule.
* **Data Quality:** The confirmation step preserves data integrity — users can't accidentally add junk values, but they *can* intentionally extend the schema or bypass ranges when needed.

**Design Choices:**
- **Enum validation** with case-insensitive matching (auto-corrects "house" → "House")
- **Dynamic enum extension & range overrides** via `EnumProposal` and `RangeProposal` + user confirmation (see §17)
- **Date parsing** with multiple format support (YYYY-MM-DD, MM/DD/YYYY, etc.)
- **Range checks** on numeric columns (e.g., Year Built: 1800–current year)
- **Warnings vs. Errors:** Soft issues (type coercion) are warnings; hard violations (wrong enum) are errors; unknown enums/out-of-range are proposals

---

## 6. Preview + Confirmation: Structurally Enforced State Machine

**Decision:** Every data mutation (insert/update/delete/undo/add-column) returns a human-readable preview and requires explicit "yes/no" confirmation before executing. Confirmations happen **inline in the chat** — no modal popups. The confirmation is now **structurally enforced** through an `AgentState` enum (see §16).

**Why:** This is critical for user trust. The before/after comparison format makes changes immediately understandable:

```
Record 'LST-5001':
  List Price: 351000 → 482000
```

**Implementation:** Tools return `requires_confirmation=True` with a rich preview string prefixed with "CONFIRMATION: ". The session transitions to `AWAITING_CONFIRMATION` state via `session.request_confirmation()`. While in this state, mutating tools are **structurally blocked** — even if the LLM hallucinates a direct tool call, it will be intercepted and rejected. The only path to executing the mutation is through `_handle_confirmation()` → `session.begin_commit()` → `_execute_confirmed_mutation()`. After execution, the agent **re-enters a mini ReAct loop** to automatically query the dataset and display the affected record(s) in a Markdown table.

**Earlier approach (discarded):** A modal popup intercepted mutation responses and presented Confirm/Cancel buttons. This was removed because it broke the natural chat flow — users expect to respond conversationally, not via UI dialogs.

**Previous approach (replaced):** The confirmation was implicitly handled — the LLM decided when to ask for confirmation. A misbehaving LLM could skip it. Now the state machine makes this architecturally impossible.

**Tradeoff:** Adds one extra round-trip for mutations. This is intentional — the alternative (auto-executing) is risky for destructive operations.

---

## 7. Undo Mechanism: JSON Write-Log

**Decision:** Track all mutations in a JSON file (`data/write_log.json`) with enough detail to reverse them.

**Log Entry Structure:**
```json
{
  "action_id": "act_c3876c18",
  "timestamp": "2026-04-22T01:30:00Z",
  "operation": "update",
  "dataset": "real_estate_listings",
  "affected_rows": [
    {"row_id": "LST-5001", "changes": {"List Price": {"before": 351000, "after": 482000}}}
  ],
  "undone": false
}
```

**Why JSON over a database:** The log is small, human-readable, and easy to inspect. For a single-user tool, JSON is simpler than setting up a database.

**Tradeoff:** If the write-log grows very large (thousands of mutations), JSON I/O becomes slow. In production, I'd use SQLite or a proper event store.

---

## 8. Session Management: In-Memory

**Decision:** Store sessions in an in-memory dictionary with automatic TTL-based cleanup.

**Why:** For a single-server deployment, in-memory sessions are fast and simple. Each session holds conversation history and pending confirmations.

**Tradeoff:** Sessions are lost on server restart. For production, I'd use Redis or a database-backed session store. The current design is appropriate for a demo/development context.

---

## 9. Structured Logging: JSON Files

**Decision:** Log every interaction as a separate JSON file in `logs/` with full details: query, reasoning steps, tool decisions, inputs, outputs, and final response.

**Why:** Full traceability is essential for debugging LLM-based systems. Each log file is self-contained and can be replayed or analyzed independently.

**What I'd change:** In production, I'd send logs to a centralized logging service (ELK, Datadog) instead of local files, and add metrics (latency histograms, tool usage counts, error rates).

---

## 10. System Prompt Design

**Decision:** The system prompt is dynamically generated with:
- Available dataset schemas (column names, types, value ranges)
- Explicit tool parameter names and usage instructions
- Explicit rules (never fabricate data, always use tools)
- Strict formatting rules requiring ALL query and mutation results (even single rows) to be displayed in Markdown tables.

**Why:** Injecting actual schema information helps the LLM make accurate tool calls. Without schema awareness, the model guesses column names and gets them wrong.

**Optimization:** Sample rows and column means were removed from the prompt to reduce token count (~500-700 tokens saved per request). The LLM can fetch samples via `query_data` and compute statistics via aggregation when needed. Explicit parameter naming (e.g., `updates` not `update_values`) was added to prevent the LLM from hallucinating incorrect parameter names.

**Tradeoff:** The system prompt is ~1,000 tokens with both schemas (down from ~1,500). Conversation history is trimmed to the last 20 messages to stay within free-tier context limits.

---

## 11. SSE Streaming Architecture

**Decision:** Implement Server-Sent Events (SSE) via a `/chat/stream` endpoint instead of returning a single JSON response.

**Event Protocol:**

| Event | Payload | Purpose |
|-------|---------|--------|
| `session_id` | `{session_id}` | Sent first so the frontend can persist the session |
| `thinking` | `{step, thought, action, observation}` | One per reasoning step — powers the live thinking block |
| `thinking_end` | `{}` | Signals reasoning is complete |
| `token` | `{token}` | Character chunks of the final response (~8 chars each) |
| `done` | `{latency_ms, requires_confirmation, ...}` | Signals end of stream |
| `error` | `{message}` | Error reporting |

**Why:** SSE gives users immediate feedback — reasoning steps appear live and the response streams character-by-character. This dramatically improves perceived latency even when actual LLM processing takes 3-5 seconds.

**Tradeoff:** SSE is unidirectional (server → client). For true bidirectional streaming (e.g., cancelling mid-stream), WebSockets would be needed. For this use case, SSE is simpler and sufficient.

---

## 12. Frontend: Vanilla JS with Live Streaming

**Decision:** Build the frontend with vanilla HTML/CSS/JS (no framework) connected to the SSE streaming backend.

**Key Design Choices:**
- **Thinking Block:** A collapsible UI element that shows reasoning steps live with a spinner, then collapses into a "Thought for N steps" summary with a clickable chevron.
- **TTFT Indicator:** A static badge in the bottom-left corner showing Time to First Token, measured client-side from request start to the first `token` event.
- **Custom Markdown Renderer:** Lightweight regex-based parser for bold, italic, code blocks, lists, and headers — avoids the ~40KB dependency of a full library like `marked.js`.
- **Session Persistence:** `sessionStorage` holds the session ID. On reload, conversation history is fetched from the API.

**Why vanilla JS:** The UI is simple enough that a framework (React, Vue) would be overhead. The entire frontend is 3 files (HTML, CSS, JS) totaling ~25KB, with zero build step.

---

## 13. Performance Optimizations

**Decision:** Optimize the full agent pipeline for latency without compromising accuracy.

| Optimization | Impact | Detail |
|---|---|---|
| **Persistent HTTP Client** | ~50-150ms saved per LLM call | Reuse `httpx.AsyncClient` across requests instead of opening a new TCP+TLS connection each time |
| **Compact Tool Observations** | ~30-40% fewer prompt tokens per turn | Truncate query results to `settings.max_observation_rows` (default 30) before feeding back to the LLM. The agent still gets enough data to compose accurate responses |
| **Cached Tool Schemas** | Minor CPU savings | Tool schemas are computed once at `__init__()` instead of every loop iteration |
| **Leaner System Prompt** | ~500-700 fewer tokens per request | Removed `sample_rows` and `mean` from the prompt schema — the LLM can fetch these via tools when needed |
| **No DataFrame Copy** | ~1-5ms per query | Removed unnecessary `.copy()` on read-only query paths |
| **Parameter Name Resilience** | Prevents wasted iterations | `update_data` accepts both `updates` and `update_values` as parameter names, since GPT-4o occasionally hallucinates the wrong name |

**Estimated Impact:** ~20-25% reduction in end-to-end latency for typical queries. For mutation queries that previously hit the parameter-name bug, latency dropped from ~22s (10 failed iterations) to ~5s (2 iterations).

---

## 14. Rate Limit Handling

**Decision:** Implement exponential backoff retry logic in `BaseLLMProvider._retry_request()` with up to 5 attempts.

**Backoff Schedule:** `[3, 8, 15, 30, 60]` seconds. Respects the `retry-after` header when the API provides one (capped at 60s).

**Why:** Free-tier LLM APIs (especially GitHub Models) have aggressive rate limits. Without retry logic, a single 429 response would crash the agent mid-conversation.

**Tradeoff:** A worst-case retry chain could take ~2 minutes. This is acceptable for a free-tier deployment — the alternative is failing entirely.

---

## 15. Provider Deduplication: OpenAICompatibleProvider

**Decision:** Consolidate all OpenAI-compatible providers (Groq, GitHub Models, OpenRouter) into a single `OpenAICompatibleProvider` base class. Each concrete provider is now a thin subclass (~10 lines) that declares only its API URL and any extra headers.

**Before:**
```
GroqProvider      → 93 lines (generate + _parse_openai_response)
GitHubModelsProvider → 96 lines (identical generate + parse)
OpenRouterProvider   → 97 lines (identical generate + parse)
```

**After:**
```
OpenAICompatibleProvider → ~80 lines (generate + parse, shared)
GroqProvider             → 5 lines  (_api_url only)
GitHubModelsProvider     → 5 lines  (_api_url only)
OpenRouterProvider       → 8 lines  (_api_url + _extra_headers)
```

**Class Hierarchy:**
```
BaseLLMProvider (abstract)
├── GeminiProvider             ← Google GenAI SDK (structurally different)
└── OpenAICompatibleProvider   ← shared generate/parse logic
    ├── GroqProvider
    ├── GitHubModelsProvider
    └── OpenRouterProvider
```

**Why:** All three providers used the exact same request format, response structure, and parsing logic. If you needed to fix a parsing bug or add streaming support, you had to edit 3 files. Now you edit one. Gemini remains separate because it uses a completely different API (Google Generative AI SDK).

**Tradeoff:** Slightly deeper inheritance hierarchy. But the alternative (3 identical copies) was a clear maintenance liability.

---

## 16. Structurally Enforced Confirmation: AgentState Machine

**Decision:** Add a formal `AgentState` enum to `Session` with three states: `IDLE`, `AWAITING_CONFIRMATION`, `COMMITTING`. The system **literally cannot** reach `COMMITTING` without passing through `AWAITING_CONFIRMATION`.

**State Transitions:**
```
IDLE → AWAITING_CONFIRMATION → COMMITTING → IDLE
                             ↑
                       only path to a write
```

**Guard Layers:**

| Guard | Location | What It Does |
|---|---|---|
| `session.is_tool_blocked(name)` | Agent ReAct loop | Rejects mutating tools if state ≠ IDLE |
| `session.begin_commit()` | `_handle_confirmation()` | Raises `RuntimeError` if state ≠ AWAITING_CONFIRMATION |
| `_execute_confirmed_mutation()` | Only called from `_handle_confirmation()` | Executes the actual DataManager write |

**Blocked Tools:** `insert_data`, `update_data`, `delete_data`, `undo_change`, `add_column` — defined in `session.MUTATING_TOOLS`.

**Why:** The previous system relied on the LLM to "choose" to ask for confirmation before mutating. A hallucinating or misbehaving LLM could theoretically skip the confirmation step and call `update_data` directly. Now this is architecturally impossible — the state machine enforces the invariant at the Python level, not the prompt level.

**Tradeoff:** Slightly more complex session logic. But the safety guarantee is worth it — no amount of prompt injection can bypass a Python `RuntimeError`.

---

## 17. Dynamic Enum Extension & Range Overrides with Confirmation

**Decision:** Allow users to add new enum values (e.g., "Twitter" as a Channel) or bypass numeric range limits through a **two-step confirmation flow**, rather than rejecting them outright.

**Flow:**

```text
1. User provides unknown enum value (e.g., Channel="Twitter") or out-of-range value
2. Validator detects it → creates EnumProposal or RangeProposal (not a hard error)
3. Tool returns requires_confirmation=True with explicit warning:
   ⚠️ New Property Type Warning / Range Override Warning
   • Channel: "Twitter" is not one of [Facebook, Instagram, ...]
   Adding this will permanently extend the dataset schema.
4. Session transitions to AWAITING_CONFIRMATION (same state machine)
5. User confirms → extend_enum() adds "Twitter" to COLUMN_SCHEMAS, or range override is applied
6. Insert/update proceeds normally and affected records are displayed
```

**Key Types:**
- `EnumProposal` and `RangeProposal` — proposal data
- `ValidationResult.new_enum_proposals` and `pending_range_proposals` — proposals requiring confirmation
- `extend_enum(dataset, column, new_value)` — permanently adds value to schema

**Why:** The original validator was too rigid — users couldn't add legitimate new values or override limits intentionally. But auto-accepting any value risks data corruption. The confirmation step strikes the right balance: explicit intent + schema integrity.

**Tradeoff:** Enum extensions are in-memory — if the server restarts, the schema reverts to defaults. For production, the extended schema should be persisted to a config file or database.

---

## What I'd Do Differently

### With More Time
1. ~~Add retry logic with exponential backoff~~ ✅ Implemented
2. ~~Implement streaming responses~~ ✅ Implemented (SSE)
3. **Add unit tests** for tools, validator, and data manager
4. ~~Build a simple web UI~~ ✅ Implemented (vanilla JS frontend)
5. **Add data visualization** — generate charts/graphs from query results
6. **Support cross-dataset queries** — "Which states have both high-value properties AND successful marketing campaigns?"

### Architecture Improvements
1. **SQLite intermediate layer** for better concurrency and crash recovery
2. **Redis session store** for persistence across restarts
3. ~~WebSocket/streaming support~~ ✅ Implemented (SSE streaming)
4. ~~Rate limiting middleware~~ ✅ Implemented (exponential backoff)
5. ~~Deduplicate OpenAI-compatible provider code~~ ✅ Implemented (`OpenAICompatibleProvider`)
6. ~~Structurally enforce confirmation flow~~ ✅ Implemented (`AgentState` machine)
7. ~~Allow dynamic enum extension with user consent~~ ✅ Implemented (`EnumProposal` & `RangeProposal` flow)
8. **Async data operations** — use async file I/O and DataFrame operations in thread pools
9. **Persist extended enum schemas** — save user-confirmed enum additions to a config file

### If This Were Production
1. **Authentication** — API keys, JWT tokens, or OAuth
2. **Multi-tenancy** — each user gets isolated data access
3. **Audit trail** — immutable log of all operations with user attribution
4. **Monitoring** — Prometheus metrics, health dashboards, alerting
5. **CI/CD** — automated testing, linting, and deployment pipeline
