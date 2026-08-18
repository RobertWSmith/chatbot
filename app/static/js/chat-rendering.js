const markdown = window.markdownit({
  html: false,
  linkify: true,
  typographer: true,
  highlight: (source, language) => {
    if (language && window.hljs && window.hljs.getLanguage(language)) {
      try {
        return window.hljs.highlight(source, { language }).value;
      } catch {
        return "";
      }
    }
    return "";
  },
});

if (window.markdownitTaskLists) {
  markdown.use(window.markdownitTaskLists);
}

function balanceMarkdown(source) {
  const fenceCount = (source.match(/```/g) || []).length;
  return fenceCount % 2 === 1 ? `${source}\n\`\`\`` : source;
}

export function renderMarkdown(target, source) {
  const dirty = markdown.render(balanceMarkdown(source));
  target.innerHTML = window.DOMPurify.sanitize(dirty);
  target.querySelectorAll("pre code").forEach((block) => {
    window.hljs.highlightElement(block);
  });
}

export function renderExistingMarkdown() {
  document.querySelectorAll("[data-markdown-source]").forEach((node) => {
    renderMarkdown(node, node.dataset.markdownSource || node.textContent);
  });
}

export function appendMessage(role, text = "") {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const label = role === "user" ? "You" : "Assistant";
  article.innerHTML = `<div class="message-role">${label}</div><div class="markdown"></div>`;
  const messageBody = article.querySelector(".markdown");
  messageBody.dataset.source = text;
  renderMarkdown(messageBody, text);
  document.querySelector("#messages").append(article);
  article.scrollIntoView({ block: "end" });
  return { article, markdown: messageBody };
}

export function ensureReasoning(article) {
  let details = article.querySelector(".reasoning");
  if (!details) {
    details = document.createElement("details");
    details.className = "reasoning";
    details.open = true;
    details.innerHTML = '<summary>Reasoning summary</summary><div class="markdown"></div>';
    article.insertBefore(details, article.querySelector(".markdown"));
  }
  return details.querySelector(".markdown");
}

export function collapseReasoning(article) {
  const details = article.querySelector(".reasoning");
  if (details) {
    details.open = false;
  }
}
