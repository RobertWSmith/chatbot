import { jsonHeaders } from "./app.js";
import { renderMcpServer, showMcpStatus, updateMcpCounts } from "./admin-mcp-view.js";

const mcpForm = document.querySelector("#mcp-server-form");
const mcpStatus = document.querySelector("#mcp-form-status");
const mcpList = document.querySelector("#mcp-server-list");
const mcpEmptyState = document.querySelector("#mcp-empty-state");
const mcpNoResults = document.querySelector("#mcp-no-results");
const mcpSearch = document.querySelector("#mcp-server-search");

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
