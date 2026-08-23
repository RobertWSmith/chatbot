# MCP Portal administration

## What must be running

The chatbot and MCP Portal are separate containers on the `chatbot_default` Compose network.
Starting the stack with `docker compose up --build` starts both services, and the chatbot waits
for the portal health check before starting.

The same portal has two addresses because the caller's network matters:

| Caller | MCP endpoint | Use for |
| --- | --- | --- |
| Chatbot container | `http://mcp-portal:8001/mcp` | The URL stored by the admin site |
| Windows host/browser tooling | `http://localhost:8001/mcp` | Host-side MCP clients and checks |
| Portal health check | `http://localhost:8001/healthz` | Host-side liveness only |

Never store `http://localhost:8001/mcp` in the chatbot admin page. From the `web` container,
`localhost` means the chatbot container itself, not MCP Portal.

## Administrator access

The MCP admin page is:

```text
http://localhost:8000/admin/mcp
```

Set the signed-in account's normalized email in `.env`:

```dotenv
PLATFORM_ADMIN_EMAILS=admin@example.com
```

Multiple administrators are comma-separated. Recreate the web container after changing this
value:

```powershell
docker compose up -d --build web
```

The first visit to an admin route promotes a configured existing account in the database. New
accounts whose email is already configured are administrators at registration time. The **MCP**
navigation item is shown to configured administrators.

## Configure the local Compose portal

Open `/admin/mcp` and choose **Configure portal for me**. The action is intentionally
idempotent and performs all required steps:

1. Creates or updates the `portal` namespace.
2. Stores transport `streamable_http` and URL `http://mcp-portal:8001/mcp`.
3. Creates a personal MCP access group for the signed-in administrator.
4. Adds the administrator as group owner.
5. Grants that group access to the portal namespace.

Then choose **Test portal tools**. A healthy portal currently advertises at least:

- `public_duckduckgo_search`
- `public_resolve_web_link`
- `public_current_date`
- `public_current_timestamp`

The LangChain adapter prefixes remote tool names for collision safety. In a chat model's tool
inventory they appear with an `mcp_portal_...` prefix.

## Manual namespace configuration

The admin form supports other remote MCP servers. The local portal's equivalent payload is:

```json
{
  "namespace": "portal",
  "display_name": "MCP Portal",
  "description": "Local Compose MCP portal",
  "transport": "streamable_http",
  "url": "http://mcp-portal:8001/mcp",
  "auth_token_env_var": null,
  "headers": {}
}
```

Creating a namespace does not grant it to anyone. Create or select a group in **Group access**,
then grant the server to that group. A user gets the deduplicated union of enabled namespaces
granted to all groups they belong to.

For authenticated servers, store only the environment variable name in
`auth_token_env_var`. Put the token itself in the web container environment. Do not store
`Authorization`, `Cookie`, or `Proxy-Authorization` values in the headers JSON.

## Relevant HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/admin/mcp` | Administrator UI |
| `GET` | `/api/admin/mcp-namespaces` | List configured namespaces |
| `POST` | `/api/admin/mcp-namespaces` | Create a namespace |
| `POST` | `/api/admin/mcp-portal/bootstrap` | Configure and grant the local portal |
| `POST` | `/api/admin/mcp-namespaces/<namespace>/test` | Discover tools from a namespace |
| `PUT` | `/api/admin/groups/<group-id>/mcp-namespaces/<namespace>` | Grant group access |
| `DELETE` | `/api/admin/groups/<group-id>/mcp-namespaces/<namespace>` | Revoke group access |
| `GET` | `/api/me/mcp-namespaces` | Show the current user's effective namespaces |

JSON mutations require the normal Flask-WTF CSRF token. The admin UI supplies it automatically.

## Chat execution behavior

For the OpenAI reasoning provider, authorized MCP tools are added directly to the standard
tool-capable agent on every message. One unavailable namespace does not suppress tools from
other namespaces.

For the custom LangGraph reasoning provider, the graph uses the portal DuckDuckGo tool while
gathering context and passes those results into later reasoning nodes. If the portal is
unavailable, the response continues without remote context and emits an unavailable status.

## Troubleshooting

### There is no MCP item in navigation

- Confirm the signed-in email is present in `PLATFORM_ADMIN_EMAILS`.
- Recreate the `web` container after editing `.env`.
- Open `/admin/mcp` directly once; this promotes an existing configured account.

### The admin test says the server cannot be reached

- Confirm `docker compose ps` reports `mcp-portal` as healthy.
- Use `docker compose logs mcp-portal` to inspect startup failures.
- Confirm the stored URL uses `mcp-portal`, not `localhost`.
- Confirm the transport is exactly `streamable_http` with an underscore.
- Rebuild the web image after dependency changes.

### The test works but chat has no MCP tools

- Check **Group access** on `/admin/mcp`; registration alone is insufficient.
- Open Settings and confirm MCP Portal appears in the account's **MCP tools** card.
- Call `GET /api/me/mcp-namespaces` while signed in to inspect the effective grant union.
- Start a new message after changing grants; tools are discovered per agent run, so no app restart
  is required.

### Search is listed but a call fails

- The portal can be healthy while its upstream DuckDuckGo request is temporarily unavailable.
- Use **Test portal tools** to distinguish discovery failures from upstream search failures.
- Inspect `docker compose logs mcp-portal` for the sanitized upstream failure category.
