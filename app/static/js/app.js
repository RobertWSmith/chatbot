function csrfToken() {
  const tag = document.querySelector('meta[name="csrf-token"]');
  return tag ? tag.content : "";
}

function jsonHeaders() {
  return {
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken(),
  };
}

window.chatApp = {
  csrfToken,
  jsonHeaders,
};
