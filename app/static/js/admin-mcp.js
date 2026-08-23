const bootstrapButton = document.querySelector("#bootstrap-portal");
const bootstrapStatus = document.querySelector("#portal-bootstrap-status");
const testStatus = document.querySelector("#mcp-test-status");
const serverForm = document.querySelector("#mcp-server-form");
const serverStatus = document.querySelector("#mcp-form-status");
const groupForm = document.querySelector("#mcp-group-form");
const grantForm = document.querySelector("#mcp-grant-form");
const grantStatus = document.querySelector("#mcp-grant-status");

async function responsePayload(response) {
  try {
    return await response.json();
  } catch (_error) {
    return {};
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...window.chatApp.jsonHeaders(), ...(options.headers || {}) },
  });
  const payload = await responsePayload(response);
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}.`);
  }
  return payload;
}

function setStatus(node, message, failed = false) {
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", failed);
}

async function testNamespace(namespace, statusNode = testStatus) {
  setStatus(statusNode, `Testing mcp_${namespace}…`);
  try {
    const payload = await requestJson(
      `/api/admin/mcp-namespaces/${encodeURIComponent(namespace)}/test`,
      { method: "POST" },
    );
    const names = payload.tools.join(", ");
    setStatus(statusNode, `Connected: ${payload.tool_count} tools (${names}).`);
    return payload;
  } catch (error) {
    setStatus(statusNode, error.message || "Connection test failed.", true);
    throw error;
  }
}

bootstrapButton?.addEventListener("click", async () => {
  bootstrapButton.disabled = true;
  setStatus(bootstrapStatus, "Registering and granting the Compose portal…");
  try {
    await requestJson("/api/admin/mcp-portal/bootstrap", { method: "POST" });
    await testNamespace("portal", bootstrapStatus);
    window.setTimeout(() => window.location.reload(), 900);
  } catch (_error) {
    // The detailed failure is already rendered by the request or probe helper.
  } finally {
    bootstrapButton.disabled = false;
  }
});

document.querySelectorAll("[data-test-namespace]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await testNamespace(button.dataset.testNamespace);
    } catch (_error) {
      // Status is shown inline.
    } finally {
      button.disabled = false;
    }
  });
});

serverForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!serverForm.reportValidity()) return;
  let headers = {};
  try {
    headers = serverForm.elements.headers.value.trim()
      ? JSON.parse(serverForm.elements.headers.value)
      : {};
  } catch (_error) {
    setStatus(serverStatus, "Headers must be valid JSON.", true);
    return;
  }
  const payload = {
    display_name: serverForm.elements.display_name.value.trim(),
    namespace: serverForm.elements.namespace.value.trim(),
    url: serverForm.elements.url.value.trim(),
    transport: serverForm.elements.transport.value,
    description: serverForm.elements.description.value.trim(),
    auth_token_env_var: serverForm.elements.auth_token_env_var.value.trim(),
    headers,
  };
  setStatus(serverStatus, "Adding server…");
  try {
    await requestJson("/api/admin/mcp-namespaces", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    window.location.reload();
  } catch (error) {
    setStatus(serverStatus, error.message || "The server could not be added.", true);
  }
});

groupForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus(grantStatus, "Creating group…");
  try {
    await requestJson("/api/groups", {
      method: "POST",
      body: JSON.stringify({ name: groupForm.elements.name.value.trim() }),
    });
    window.location.reload();
  } catch (error) {
    setStatus(grantStatus, error.message || "The group could not be created.", true);
  }
});

grantForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const groupId = grantForm.elements.group_id.value;
  const namespace = grantForm.elements.namespace.value;
  setStatus(grantStatus, "Granting access…");
  try {
    await requestJson(
      `/api/admin/groups/${encodeURIComponent(groupId)}/mcp-namespaces/${encodeURIComponent(namespace)}`,
      { method: "PUT" },
    );
    window.location.reload();
  } catch (error) {
    setStatus(grantStatus, error.message || "Access could not be granted.", true);
  }
});

document.querySelectorAll("[data-revoke-grant]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("Revoke this MCP grant?")) return;
    setStatus(grantStatus, "Revoking access…");
    try {
      await requestJson(
        `/api/admin/groups/${encodeURIComponent(button.dataset.groupId)}/mcp-namespaces/${encodeURIComponent(button.dataset.namespace)}`,
        { method: "DELETE" },
      );
      window.location.reload();
    } catch (error) {
      setStatus(grantStatus, error.message || "Access could not be revoked.", true);
    }
  });
});
