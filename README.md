# Flask LangGraph Chatbot

A Flask chatbot app with email/password auth, Postgres persistence, LangGraph chat streaming, OpenAI reasoning summaries, streaming Markdown, per-user settings, and user-approved long-term memory.

## Quick Start

1. Create and activate a Python 3.11+ virtual environment.
2. Install the project and its development dependencies:

   ```powershell
   pip install -e ".[dev]"
   ```

   For a production-only installation, use `pip install .` instead.

3. Copy `.env.example` to `.env` and set:

   ```text
   SECRET_KEY=...
   DATABASE_URL=postgresql+psycopg://...
   LANGGRAPH_DATABASE_URL=postgresql://...
   OPENAI_API_KEY=...
   CHAT_MODEL_PROVIDER=openai
   CUSTOM_REASONING_GRAPH_ENABLED=1
   CUSTOM_REASONING_MAX_RESEARCH_ROUNDS=2
   MEMORY_EMBEDDING_MODEL=text-embedding-3-small
   MEMORY_EMBEDDING_DIMENSIONS=1536
   CONVERSATION_HISTORY_LIMIT=24
   PLATFORM_ADMIN_EMAILS=admin@example.com
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

## Tests

Run the complete suite from the project root:

```powershell
python -m coverage run -m pytest
python -m coverage report
python -m ruff check .
python -m black --check .
```

The tests exercise authentication and password migration, chat streaming and telemetry,
short- and long-term memory, settings validation, reasoning workflows, public web URL
safety, and multi-tenant MCP authorization. The code-quality tests also require every
Python function, method, and class—including test helpers and migrations—to retain a
docstring with basic Google-style summary and section formatting. The configured coverage
check fails below 80% application statement coverage.

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

- `none`: answer directly
- `low`: gather context, then answer
- `medium`: gather context, plan, then answer
- `high`: gather context, plan, draft, critique, finalize, then check memory proposals
- `xhigh`: high effort plus an alternate draft before critique/finalization
- `max`: the deepest available workflow, currently matching `xhigh`

The custom graph currently uses `CHAT_MODEL_PROVIDER=openai`, but model construction is
isolated in the service layer so additional providers can be added without changing the
reasoning workflow.

When the user has MCP access, the custom graph makes a no-tool routing decision before
answering. Only its dedicated `mcp_research` node receives MCP tools, and that node accepts
only tools explicitly annotated as read-only and non-destructive. Planning, drafting,
critique, finalization, and memory nodes use a separate unbound model with no MCP tools.
High, xhigh, and max workflows can return to research after critique, bounded by
`CUSTOM_REASONING_MAX_RESEARCH_ROUNDS` (default `2`).

## Multi-tenant MCP access

MCP access is granted through groups. A user can belong to any number of groups and gets
the deduplicated union of every enabled MCP namespace assigned to those groups. The union
is resolved again at the start of every agent run, so removing a membership or namespace
grant affects the next message without requiring the user to sign in again.

Set `PLATFORM_ADMIN_EMAILS` before registering the initial platform administrator. Existing
accounts can instead be promoted by setting `users.is_platform_admin` directly. The flow is:

1. An authenticated user creates a group with `POST /api/groups` and becomes its owner.
2. A group owner creates a one-time, optionally email-bound invitation with
   `POST /api/groups/<group-id>/invitations`.
3. The invited authenticated user submits its token to `POST /api/groups/join`.
4. A platform administrator creates a remote MCP namespace with
   `POST /api/admin/mcp-namespaces`, then grants it using
   `PUT /api/admin/groups/<group-id>/mcp-namespaces/<namespace>`.

Platform administrators can also open `/admin/mcp` to view the server inventory and add
remote MCP servers through the admin UI. MCP request and response payloads are validated
with Pydantic; bearer tokens are still referenced by environment-variable name and are
never submitted to or stored by the application.

`PLATFORM_ADMIN_EMAILS` is also checked when an authenticated user opens an admin route,
so an existing account becomes an administrator after its email is added and the Flask
process is restarted. Other signed-in users see an access screen with a link to switch
accounts instead of Flask's generic forbidden page.

Example namespace request:

```json
{
  "namespace": "billing",
  "display_name": "Billing",
  "transport": "http",
  "url": "https://mcp.example.com/mcp",
  "auth_token_env_var": "BILLING_MCP_TOKEN",
  "headers": {"X-Client": "chatbot"}
}
```

Docker Compose builds the sibling `../mcp-portal` project and runs it on the shared
Compose network. Its MCP addresses are:

- From the chatbot container: `http://mcp-portal:8001/mcp`
- From the host: `http://localhost:8001/mcp` (or the port set by
  `MCP_PORTAL_HOST_PORT`)

The portal prints both addresses during startup. View them with:

```powershell
docker compose logs mcp-portal
```

Configure the chatbot namespace with `streamable_http` (underscore) as the transport:

```json
{
  "namespace": "portal",
  "display_name": "MCP Portal",
  "transport": "streamable_http",
  "url": "http://mcp-portal:8001/mcp",
  "headers": {}
}
```

The Compose defaults intentionally use no portal authentication and no portal database
backend for local development. Configure an authentication provider before exposing the
portal beyond the local machine or a trusted container network.

Set `BILLING_MCP_TOKEN` in the web process environment. The database stores only that
environment-variable name, not the bearer token. Only remote HTTP/SSE transports are
accepted by the application. Loaded tools are prefixed as `mcp_<namespace>_...`, which
keeps identically named tools from separate tenant integrations distinct.

Useful authenticated endpoints:

- `GET /api/groups` lists the current user's groups and per-group namespace grants.
- `GET /api/groups/<group-id>/members` lists group members.
- `DELETE /api/groups/<group-id>/members/<user-id>` revokes membership (owner only).
- `GET /api/me/mcp-namespaces` returns the current user's effective namespace union.
- `DELETE /api/admin/groups/<group-id>/mcp-namespaces/<namespace>` revokes a grant.

With the custom reasoning graph enabled, MCP namespace grants remain on the custom graph and
feed only its read-only `mcp_research` node. With the custom graph disabled, users continue to
use the standard tool-capable agent.

### Tool-call logging

The web process logs the selected agent mode, authorized MCP namespaces, discovered tool
inventory, and every actual tool start, success, or failure. Follow the audit stream with:

```powershell
docker compose logs -f web
```

Look for `event=agent.mode` and `event=agent.tool_inventory` first. An actual invocation
produces matching `event=tool.call.start` and `event=tool.call.end` records with the tool
name, MCP provenance when available, run identifier, duration, and output size. MCP
connection and discovery events use the `event=mcp.*` prefix. Prompt text and tool result
content are not logged.

`LOG_LEVEL` defaults to `INFO`. Tool argument values are omitted by default; set
`TOOL_CALL_LOG_ARGUMENTS=1` only for local diagnosis when sanitized, 500-character-bounded
argument values are needed. Keys containing token, secret, password, credential, API-key,
authorization, or cookie markers are redacted.
