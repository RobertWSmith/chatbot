import { jsonHeaders } from "./app.js";
import { renderMcpServer, showMcpStatus, updateMcpCounts } from "./admin-mcp-view.js";

const mcpForm = document.querySelector("#mcp-server-form");
const mcpStatus = document.querySelector("#mcp-form-status");
const mcpList = document.querySelector("#mcp-server-list");
const mcpEmptyState = document.querySelector("#mcp-empty-state");
const mcpNoResults = document.querySelector("#mcp-no-results");
const mcpSearch = document.querySelector("#mcp-server-search");
const mcpGrantForm = document.querySelector("#mcp-grant-form");
const mcpGrantStatus = document.querySelector("#mcp-grant-status");
const mcpGrantList = document.querySelector("#mcp-grant-list");
const mcpGrantHelp = document.querySelector("#mcp-grant-help");
const mcpGroupForm = document.querySelector("#mcp-group-form");
const mcpGroupStatus = document.querySelector("#mcp-group-status");

async function responseError(response, fallback) {
  try {
    const payload = await response.json();
    return payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

function addServerToGrantForm(item) {
  if (!mcpGrantForm) return;
  const serverSelect = mcpGrantForm.elements.namespace;
  const groupSelect = mcpGrantForm.elements.group_id;
  const option = document.createElement("option");
  option.value = item.namespace;
  option.textContent = `${item.display_name} · mcp_${item.namespace}`;
  serverSelect.append(option);
  serverSelect.disabled = false;
  if (groupSelect.options.length > 1) {
    mcpGrantForm.querySelector("button[type='submit']").disabled = false;
  }
}

function parseHeaders(value) {
  if (!value.trim()) return {};
  const parsed = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Request headers must be a JSON object.");
  }
  return parsed;
}

function slugifyNamespace(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .replace(/^[^a-z]+/, "")
    .slice(0, 31);
}

document.querySelectorAll("[data-focus-target]").forEach((link) => {
  link.addEventListener("click", () => {
    window.setTimeout(() => mcpForm?.elements[link.dataset.focusTarget]?.focus(), 150);
  });
});

if (mcpSearch) {
  mcpSearch.addEventListener("input", () => {
    const query = mcpSearch.value.trim().toLowerCase();
    const cards = [...mcpList.querySelectorAll(".admin-server-card")];
    let visible = 0;
    cards.forEach((card) => {
      const matches = !query || card.dataset.searchValue.includes(query);
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    mcpNoResults.hidden = visible > 0 || cards.length === 0;
  });
}

if (mcpForm) {
  let namespaceWasEdited = false;
  mcpForm.elements.namespace.addEventListener("input", () => {
    namespaceWasEdited = Boolean(mcpForm.elements.namespace.value);
  });
  mcpForm.elements.display_name.addEventListener("input", () => {
    if (!namespaceWasEdited) {
      mcpForm.elements.namespace.value = slugifyNamespace(mcpForm.elements.display_name.value);
    }
  });

  mcpForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMcpStatus(mcpStatus, "");
    if (!mcpForm.reportValidity()) return;

    let headers;
    try {
      headers = parseHeaders(mcpForm.elements.headers.value);
    } catch (error) {
      showMcpStatus(
        mcpStatus,
        error.message || "Request headers are not valid JSON.",
        "error",
      );
      mcpForm.elements.headers.focus();
      return;
    }

    const button = mcpForm.querySelector("button[type='submit']");
    const buttonLabel = button.querySelector("span");
    button.disabled = true;
    buttonLabel.textContent = "Adding server…";

    const payload = {
      display_name: mcpForm.elements.display_name.value.trim(),
      namespace: mcpForm.elements.namespace.value.trim(),
      url: mcpForm.elements.url.value.trim(),
      transport: mcpForm.elements.transport.value,
      description: mcpForm.elements.description.value.trim(),
      auth_token_env_var: mcpForm.elements.auth_token_env_var.value.trim(),
      headers,
    };

    try {
      const response = await fetch("/api/admin/mcp-namespaces", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The server could not be added.");

      renderMcpServer(mcpList, result.mcp_namespace);
      updateMcpCounts(result.mcp_namespace);
      addServerToGrantForm(result.mcp_namespace);
      mcpEmptyState.hidden = true;
      mcpNoResults.hidden = true;
      mcpSearch.value = "";
      mcpForm.reset();
      namespaceWasEdited = false;
      showMcpStatus(
        mcpStatus,
        `${result.mcp_namespace.display_name} was added successfully.`,
        "success",
      );
    } catch (error) {
      showMcpStatus(mcpStatus, error.message || "The server could not be added.", "error");
    } finally {
      button.disabled = false;
      buttonLabel.textContent = "Add MCP server";
    }
  });
}

if (mcpGrantForm) {
  mcpGrantForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMcpStatus(mcpGrantStatus, "");
    if (!mcpGrantForm.reportValidity()) return;

    const namespace = mcpGrantForm.elements.namespace.value;
    const groupId = mcpGrantForm.elements.group_id.value;
    const button = mcpGrantForm.querySelector("button[type='submit']");
    button.disabled = true;
    button.textContent = "Granting access…";

    try {
      const response = await fetch(
        `/api/admin/groups/${encodeURIComponent(groupId)}/mcp-namespaces/${encodeURIComponent(namespace)}`,
        { method: "PUT", headers: jsonHeaders() },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "Access could not be granted."));
      }
      showMcpStatus(mcpGrantStatus, "Group access granted. Refreshing…", "success");
      window.setTimeout(() => window.location.reload(), 250);
    } catch (error) {
      showMcpStatus(mcpGrantStatus, error.message || "Access could not be granted.", "error");
      button.disabled = false;
      button.textContent = "Grant access";
    }
  });
}

if (mcpGroupForm) {
  mcpGroupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showMcpStatus(mcpGroupStatus, "");
    if (!mcpGroupForm.reportValidity()) return;

    const button = mcpGroupForm.querySelector("button[type='submit']");
    button.disabled = true;
    button.textContent = "Creating…";
    try {
      const response = await fetch("/api/groups", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ name: mcpGroupForm.elements.name.value.trim() }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response, "The tenant group could not be created."));
      }
      const result = await response.json();
      const groupSelect = mcpGrantForm.elements.group_id;
      const option = document.createElement("option");
      option.value = result.group.id;
      option.textContent = `${result.group.name} · ${result.group.slug}`;
      option.selected = true;
      groupSelect.append(option);
      groupSelect.disabled = false;
      if (mcpGrantForm.elements.namespace.options.length > 1) {
        mcpGrantForm.querySelector("button[type='submit']").disabled = false;
      }
      mcpGrantHelp.textContent =
        "Access is recalculated on the group's next chat message; no restart is needed.";
      mcpGroupForm.reset();
      showMcpStatus(mcpGroupStatus, `${result.group.name} was created and selected.`, "success");
    } catch (error) {
      showMcpStatus(
        mcpGroupStatus,
        error.message || "The tenant group could not be created.",
        "error",
      );
    } finally {
      button.disabled = false;
      button.textContent = "Create group";
    }
  });
}

if (mcpGrantList) {
  mcpGrantList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-revoke-grant]");
    if (!button) return;

    const confirmed = window.confirm(
      `Revoke ${button.dataset.groupName} access to ${button.dataset.serverName}?`,
    );
    if (!confirmed) return;

    showMcpStatus(mcpGrantStatus, "");
    button.disabled = true;
    button.textContent = "Revoking…";
    try {
      const response = await fetch(
        `/api/admin/groups/${encodeURIComponent(button.dataset.groupId)}/mcp-namespaces/${encodeURIComponent(button.dataset.namespace)}`,
        { method: "DELETE", headers: jsonHeaders() },
      );
      if (!response.ok) {
        throw new Error(await responseError(response, "Access could not be revoked."));
      }
      showMcpStatus(mcpGrantStatus, "Group access revoked. Refreshing…", "success");
      window.setTimeout(() => window.location.reload(), 250);
    } catch (error) {
      showMcpStatus(mcpGrantStatus, error.message || "Access could not be revoked.", "error");
      button.disabled = false;
      button.textContent = "Revoke";
    }
  });
}
