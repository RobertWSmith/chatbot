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
   CHAT_MODEL_PROVIDER=openai
   CUSTOM_REASONING_GRAPH_ENABLED=0
   MEMORY_EMBEDDING_MODEL=text-embedding-3-small
   MEMORY_EMBEDDING_DIMENSIONS=1536
   CONVERSATION_HISTORY_LIMIT=24
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

By default, the app uses LangChain's prebuilt `create_agent` flow and passes the user's
`reasoning_effort` setting through to OpenAI as provider-native reasoning configuration.
The app surfaces reasoning summaries when the configured model/provider supports them. It
does not expose raw hidden reasoning.

Set `CUSTOM_REASONING_GRAPH_ENABLED=1` to use the app-owned LangGraph workflow instead.
That path treats `reasoning_effort` as graph topology:

- `minimal`: answer directly
- `low`: gather context, then answer
- `medium`: gather context, plan, then answer
- `high`: gather context, plan, draft, critique, finalize, then check memory proposals
- `xhigh`: high effort plus an alternate draft before critique/finalization

The custom graph currently uses `CHAT_MODEL_PROVIDER=openai`, but model construction is
isolated in the service layer so additional providers can be added without changing the
reasoning workflow.
