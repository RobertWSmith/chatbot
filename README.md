# Flask LangGraph Chatbot

A Flask chatbot app with email/password auth, Postgres persistence, LangGraph chat streaming, OpenAI reasoning summaries, streaming Markdown, per-user settings, and user-approved long-term memory.

## Quick Start

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set:

   ```text
   SECRET_KEY=...
   DATABASE_URL=postgresql+psycopg://...
   LANGGRAPH_DATABASE_URL=postgresql://...
   OPENAI_API_KEY=...
   MEMORY_EMBEDDING_MODEL=text-embedding-3-small
   MEMORY_EMBEDDING_DIMENSIONS=1536
   CONVERSATION_HISTORY_LIMIT=24
   TOOL_MAX_CONCURRENCY=4
   ```

4. Run migrations:

   ```powershell
   flask --app run.py db upgrade
   ```

5. Start the app:

   ```powershell
   flask --app run.py run
   ```

If `OPENAI_API_KEY` is empty, chat uses local demo streaming so the UI and auth flow can be tested without model calls.

## Memory

Short-term conversation memory is stored in Postgres as chat messages and replayed into each agent call for the active thread. `CONVERSATION_HISTORY_LIMIT` controls how many recent turns are sent back verbatim. When a thread exceeds that limit, older turns are compacted into rolling `conversation_memory_snapshots` summaries and prepended to the agent context.

Long-term memory is deliberately user-approved. The agent can create pending proposals with `propose_memory`, but approved memories are embedded and stored in Postgres with pgvector. The `recall_user_memory` tool retrieves approved memories as a small RAG pipeline scoped to the current user.

The Docker Compose database uses `pgvector/pgvector:0.8.2-pg17`, and migrations create the `vector` extension plus the `long_term_memories` table.

## Reasoning

Each account has a default reasoning provider, and each chat can override it from the
composer. `OpenAI` uses provider-native reasoning through the standard tool-capable agent.
`LangGraph` uses the app-owned workflow and treats reasoning effort as graph depth, from a
direct answer at `minimal` through planning, drafting, critique, and finalization at the
higher levels. The selection is persisted with the chat rather than configured when the
process launches.

The app surfaces user-safe reasoning summaries when the selected workflow supports them.
It does not expose raw hidden reasoning.

## Tool concurrency

Independent tool calls emitted in the same model turn run concurrently through LangGraph, up to
`TOOL_MAX_CONCURRENCY` workers (default `4`, clamped to `1`–`16`). Calls with dependencies still
run in separate turns—for example, web search must return a URL before the resolver can open it.
Database-backed memory tools are serialized within each agent run because they share a
Flask-SQLAlchemy session; network-only search and URL resolution calls remain concurrent.

## MCP Portal

Docker Compose builds the sibling `../mcp-portal` project and starts it with the chatbot.
The portal includes the `public_duckduckgo_search` and `public_resolve_web_link` tools.

Start or rebuild the complete stack with:

```powershell
docker compose up --build
```

The MCP endpoint is available at:

- From the chatbot container: `http://mcp-portal:8001/mcp`
- From the host: `http://localhost:8001/mcp` (or the port set by
  `MCP_PORTAL_HOST_PORT`)

For an MCP client, configure the portal as a streamable HTTP server:

```json
{
  "namespace": "portal",
  "display_name": "MCP Portal",
  "transport": "streamable_http",
  "url": "http://mcp-portal:8001/mcp",
  "headers": {}
}
```

The Compose defaults use no portal authentication and no portal database backend for local
development. Configure an authentication provider before exposing the portal beyond the local
machine or its trusted container network.

Platform administrators configure and grant MCP namespaces at `/admin/mcp`. Set
`PLATFORM_ADMIN_EMAILS`, restart the web service, and use **Configure portal for me** for the
local Compose portal. See [docs/mcp-portal.md](docs/mcp-portal.md) for the complete runbook and
troubleshooting guide.
