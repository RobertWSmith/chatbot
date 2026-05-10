const md = window.markdownit({
  html: false,
  linkify: true,
  typographer: true,
  highlight: (str, lang) => {
    if (lang && window.hljs && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value;
      } catch {
        return "";
      }
    }
    return "";
  },
});

if (window.markdownitTaskLists) {
  md.use(window.markdownitTaskLists);
}

function renderMarkdown(target, source) {
  const balanced = balanceMarkdown(source);
  const dirty = md.render(balanced);
  target.innerHTML = DOMPurify.sanitize(dirty);
  target.querySelectorAll("pre code").forEach((block) => hljs.highlightElement(block));
}

function balanceMarkdown(source) {
  const fenceCount = (source.match(/```/g) || []).length;
  return fenceCount % 2 === 1 ? `${source}\n\`\`\`` : source;
}

function appendMessage(role, text = "") {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  article.innerHTML = `<div class="message-role">${role}</div><div class="markdown"></div>`;
  const markdown = article.querySelector(".markdown");
  markdown.dataset.source = text;
  renderMarkdown(markdown, text);
  document.querySelector("#messages").append(article);
  article.scrollIntoView({ block: "end" });
  return { article, markdown };
}

function ensureReasoning(article) {
  let details = article.querySelector(".reasoning");
  if (!details) {
    details = document.createElement("details");
    details.className = "reasoning";
    details.open = true;
    details.innerHTML = "<summary>Reasoning summary</summary><div class=\"markdown\"></div>";
    article.insertBefore(details, article.querySelector(".markdown"));
  }
  return details.querySelector(".markdown");
}

async function readSse(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    for (const part of parts) {
      const eventLine = part.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      onEvent(eventLine.slice(7), JSON.parse(dataLine.slice(6)));
    }
  }
}

document.querySelectorAll("[data-markdown-source]").forEach((node) => {
  renderMarkdown(node, node.dataset.markdownSource || node.textContent);
});

const form = document.querySelector("#chat-form");
if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const pane = document.querySelector(".chat-pane");
    const input = document.querySelector("#message-input");
    const status = document.querySelector("#stream-status");
    const message = input.value.trim();
    if (!message) return;
    input.value = "";
    appendMessage("user", message);
    const assistant = appendMessage("assistant", "");
    let assistantSource = "";
    let reasoningSource = "";
    status.textContent = "Starting";

    const response = await fetch(`/api/chat/threads/${pane.dataset.threadId}/messages`, {
      method: "POST",
      headers: window.chatApp.jsonHeaders(),
      body: JSON.stringify({ message }),
    });
    if (!response.ok) {
      status.textContent = "Message failed to send.";
      return;
    }

    await readSse(response, (eventName, payload) => {
      if (eventName === "token") {
        assistantSource += payload.text || "";
        renderMarkdown(assistant.markdown, assistantSource);
      } else if (eventName === "reasoning_summary") {
        reasoningSource += payload.text || "";
        renderMarkdown(ensureReasoning(assistant.article), reasoningSource);
      } else if (eventName === "status") {
        status.textContent = payload.text || "";
      } else if (eventName === "memory_proposal") {
        status.textContent = "Memory proposal created. Review it on the Memory page.";
      } else if (eventName === "error") {
        status.textContent = payload.message || "Stream failed.";
      } else if (eventName === "done") {
        status.textContent = "Done";
      }
      assistant.article.scrollIntoView({ block: "end" });
    });
  });
}

const newThread = document.querySelector("#new-thread");
if (newThread) {
  newThread.addEventListener("click", async () => {
    const response = await fetch("/api/chat/threads", {
      method: "POST",
      headers: window.chatApp.jsonHeaders(),
    });
    if (response.ok) {
      const payload = await response.json();
      window.location.href = `/chat/${payload.thread.id}`;
    }
  });
}
