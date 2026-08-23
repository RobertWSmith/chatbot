export function showMcpStatus(statusNode, message, kind = "") {
  if (!statusNode) return;
  statusNode.textContent = message;
  statusNode.className = `admin-form-status ${kind}`.trim();
}

export function updateMcpCounts(item) {
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

export function renderMcpServer(list, item) {
  const card = document.createElement("article");
  card.className = "admin-server-card";
  card.dataset.namespace = item.namespace;
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
  const grantCount = document.createElement("span");
  grantCount.dataset.serverGrantCount = "";
  const grantCountLabel = document.createElement("strong");
  grantCountLabel.textContent = "Group access";
  grantCount.append(
    grantCountLabel,
    document.createTextNode(String(item.group_grant_count || 0)),
  );
  meta.append(grantCount);
  content.append(meta);
  const testButton = document.createElement("button");
  testButton.className = "secondary mt-3";
  testButton.type = "button";
  testButton.dataset.testNamespace = item.namespace;
  testButton.textContent = "Test tools";
  content.append(testButton);
  card.append(mark, content);
  list.append(card);
}
