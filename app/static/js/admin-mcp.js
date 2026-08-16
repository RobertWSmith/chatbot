const mcpForm = document.querySelector("#mcp-server-form");
const mcpStatus = document.querySelector("#mcp-form-status");
const mcpList = document.querySelector("#mcp-server-list");
const mcpEmptyState = document.querySelector("#mcp-empty-state");
const mcpNoResults = document.querySelector("#mcp-no-results");
const mcpSearch = document.querySelector("#mcp-server-search");

function showMcpStatus(message, kind = "") {
  if (!mcpStatus) return;
  mcpStatus.textContent = message;
  mcpStatus.className = `admin-form-status ${kind}`.trim();
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

function updateMcpCounts(item) {
  const total = document.querySelector("#mcp-total-count");
  const enabled = document.querySelector("#mcp-enabled-count");
  const secured = document.querySelector("#mcp-secured-count");
  if (total) total.textContent = String(Number(total.textContent) + 1);
  if (enabled && item.enabled) enabled.textContent = String(Number(enabled.textContent) + 1);
  if (secured && item.auth_token_env_var) {
    secured.textContent = String(Number(secured.textContent) + 1);
  }
}

function createSvg(pathData) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("viewBox", "0 0 20 20");
  pathData.forEach((data) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", data);
    svg.append(path);
  });
  return svg;
}

function addMeta(container, label, value) {
  const item = document.createElement("span");
  const strong = document.createElement("strong");
  strong.textContent = label;
  item.append(strong, document.createTextNode(value));
  container.append(item);
}

function renderMcpServer(item) {
  const card = document.createElement("article");
  card.className = "admin-server-card";
  card.dataset.searchValue = [item.display_name, item.namespace, item.description, item.url]
    .join(" ")
    .toLowerCase();

  const mark = document.createElement("div");
  mark.className = "admin-server-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = item.display_name.charAt(0).toUpperCase();

  const content = document.createElement("div");
  content.className = "admin-server-content";
  const titleRow = document.createElement("div");
  titleRow.className = "admin-server-title-row";
  const title = document.createElement("div");
  const heading = document.createElement("h3");
  heading.textContent = item.display_name;
  const namespace = document.createElement("code");
  namespace.textContent = `mcp_${item.namespace}`;
  title.append(heading, namespace);
  const status = document.createElement("span");
  status.className = `admin-status-pill ${item.enabled ? "enabled" : "disabled"}`;
  const statusDot = document.createElement("span");
  status.append(statusDot, document.createTextNode(item.enabled ? "Enabled" : "Disabled"));
  titleRow.append(title, status);
  content.append(titleRow);

  if (item.description) {
    const description = document.createElement("p");
    description.className = "admin-server-description";
    description.textContent = item.description;
    content.append(description);
  }

  const url = document.createElement("a");
  url.className = "admin-server-url";
  url.href = item.url;
  url.target = "_blank";
  url.rel = "noreferrer";
  url.title = item.url;
  url.append(createSvg(["M8 12 12 8M7 5h-1a4 4 0 0 0 0 8h2M13 15h1a4 4 0 0 0 0-8h-2"]));
  const urlText = document.createElement("span");
  urlText.textContent = item.url;
  url.append(urlText);
  content.append(url);

  const meta = document.createElement("div");
  meta.className = "admin-server-meta";
  addMeta(meta, "Transport", item.transport.replaceAll("_", " "));
  addMeta(meta, "Authentication", item.auth_token_env_var || "None");
  addMeta(meta, "Headers", String(Object.keys(item.headers || {}).length));
  addMeta(meta, "Group access", String(item.group_grant_count || 0));
  content.append(meta);
  card.append(mark, content);
  mcpList.append(card);
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
    showMcpStatus("");
    if (!mcpForm.reportValidity()) return;

    let headers;
    try {
      headers = parseHeaders(mcpForm.elements.headers.value);
    } catch (error) {
      showMcpStatus(error.message || "Request headers are not valid JSON.", "error");
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
        headers: window.chatApp.jsonHeaders(),
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The server could not be added.");

      renderMcpServer(result.mcp_namespace);
      updateMcpCounts(result.mcp_namespace);
      mcpEmptyState.hidden = true;
      mcpNoResults.hidden = true;
      mcpSearch.value = "";
      mcpForm.reset();
      namespaceWasEdited = false;
      showMcpStatus(`${result.mcp_namespace.display_name} was added successfully.`, "success");
    } catch (error) {
      showMcpStatus(error.message || "The server could not be added.", "error");
    } finally {
      button.disabled = false;
      buttonLabel.textContent = "Add MCP server";
    }
  });
}
