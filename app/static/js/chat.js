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

if (window.texmath && window.katex) {
  md.use(window.texmath, {
    engine: window.katex,
    delimiters: ["dollars", "brackets"],
    katexOptions: {
      throwOnError: false,
    },
  });
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
  const label = role === "user" ? "You" : "Assistant";
  article.innerHTML = `<div class="message-role">${label}</div><div class="markdown"></div>`;
  const markdown = article.querySelector(".markdown");
  markdown.dataset.source = text;
  renderMarkdown(markdown, text);
  document.querySelector("#messages").append(article);
  article.scrollIntoView({ block: "end" });
  return { article, markdown };
}

renderExistingMarkdown();

const form = document.querySelector("#chat-form");
if (form) {
  const input = document.querySelector("#message-input");
  const submitButton = form.querySelector("button[type='submit']");
  const reasoningProviderSelect = document.querySelector("#reasoning-provider-select");
  const modelSelect = document.querySelector("#model-select");
  const reasoningSelect = document.querySelector("#reasoning-select");

  function resizeComposer() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
  }

  function setStreaming(isStreaming) {
    input.disabled = isStreaming;
    submitButton.disabled = isStreaming;
    reasoningProviderSelect.disabled = isStreaming;
    modelSelect.disabled = isStreaming;
    reasoningSelect.disabled = isStreaming;
    submitButton.textContent = isStreaming ? "Sending" : "Send";
    form.classList.toggle("is-streaming", isStreaming);
    form.setAttribute("aria-busy", String(isStreaming));
  }

  input.addEventListener("input", resizeComposer);
  input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      if (!submitButton.disabled) {
        form.requestSubmit();
      }
    }
  });
  resizeComposer();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const pane = document.querySelector(".chat-pane");
    const status = document.querySelector("#stream-status");
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    resizeComposer();
    appendMessage("user", message);
    const assistant = appendMessage("assistant", "");
    let assistantSource = "";
    let reasoningSource = "";
    status.textContent = "Starting";
    setStreaming(true);

    try {
      const response = await fetch(`/api/chat/threads/${pane.dataset.threadId}/messages`, {
        method: "POST",
        headers: window.chatApp.jsonHeaders(),
        body: JSON.stringify({
          message,
          reasoning_provider: reasoningProviderSelect.value,
          model_name: modelSelect.value,
          reasoning_effort: reasoningSelect.value,
        }),
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
          collapseReasoning(assistant.article);
        }
      });
    } catch {
      status.textContent = "Message failed to send.";
    } finally {
      setStreaming(false);
      input.focus();
    }
  });
}

const newThread = document.querySelector("#new-thread");
if (newThread) {
  newThread.addEventListener("click", async () => {
    const response = await fetch("/api/chat/threads", {
      method: "POST",
      headers: jsonHeaders(),
    });
    if (response.ok) {
      const payload = await response.json();
      window.location.href = `/chat/${payload.thread.id}`;
    }
  });
}
