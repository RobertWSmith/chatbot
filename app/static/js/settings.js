const form = document.querySelector("#settings-form");
const statusNode = document.querySelector("#settings-status");

function setNested(target, dottedName, value) {
  const parts = dottedName.split(".");
  let cursor = target;
  while (parts.length > 1) {
    const part = parts.shift();
    cursor[part] = cursor[part] || {};
    cursor = cursor[part];
  }
  cursor[parts[0]] = value;
}

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = {};
    form.querySelectorAll("select, input[type='checkbox']").forEach((field) => {
      setNested(data, field.name, field.type === "checkbox" ? field.checked : field.value);
    });
    const response = await fetch("/api/settings", {
      method: "PATCH",
      headers: window.chatApp.jsonHeaders(),
      body: JSON.stringify(data),
    });
    if (response.ok) {
      statusNode.textContent = "Settings saved.";
    } else {
      const payload = await response.json();
      statusNode.textContent = JSON.stringify(payload.errors || payload);
    }
  });
}
