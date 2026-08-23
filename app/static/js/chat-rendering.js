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

if (window.texmath && window.katex) {
  markdown.use(window.texmath, {
    engine: window.katex,
    delimiters: ["dollars", "brackets"],
    katexOptions: { throwOnError: false },
  });
}

function splitFusedHeadings(line) {
  const parts = [];
  let partStart = 0;
  let inlineCodeTicks = 0;

  for (let index = 0; index < line.length; index += 1) {
    if (line[index] === "`") {
      let tickCount = 1;
      while (line[index + tickCount] === "`") tickCount += 1;
      if (inlineCodeTicks === 0) {
        inlineCodeTicks = tickCount;
      } else if (inlineCodeTicks === tickCount) {
        inlineCodeTicks = 0;
      }
      index += tickCount - 1;
      continue;
    }
    if (
      inlineCodeTicks !== 0 ||
      line[index] !== "#" ||
      line[index - 1] === "#" ||
      line[index - 1] === "\\"
    ) {
      continue;
    }

    let hashCount = 1;
    while (line[index + hashCount] === "#") hashCount += 1;
    const followsHeadingSyntax =
      hashCount <= 6 &&
      /[\t ]/.test(line[index + hashCount] || "") &&
      line.slice(index + hashCount).trim().length > 0;
    if (!followsHeadingSyntax) continue;

    let previousIndex = index - 1;
    while (previousIndex >= partStart && /[\t ]/.test(line[previousIndex])) {
      previousIndex -= 1;
    }
    if (previousIndex < partStart) continue;

    const previousCharacter = line[previousIndex];
    const looksLikeBoundary = hashCount >= 2 || /[.!?;:)\]}]/.test(previousCharacter);
    if (!looksLikeBoundary) continue;

    const precedingText = line.slice(partStart, index).trimEnd();
    if (precedingText) parts.push(precedingText);
    partStart = index;
    index += hashCount - 1;
  }

  parts.push(line.slice(partStart));
  return parts;
}

export function normalizeMarkdown(source) {
  const lines = String(source || "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  const normalized = [];
  let fence = null;

  lines.forEach((line) => {
    const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      normalized.push(line);
      if (
        fenceMatch &&
        fenceMatch[1][0] === fence.character &&
        fenceMatch[1].length >= fence.size
      ) {
        fence = null;
      }
      return;
    }
    if (fenceMatch) {
      normalized.push(line);
      fence = { character: fenceMatch[1][0], size: fenceMatch[1].length };
      return;
    }

    splitFusedHeadings(line).forEach((part) => {
      if (/^#{1,6}[\t ]+\S/.test(part) && normalized.at(-1)?.trim()) {
        normalized.push("");
      }
      normalized.push(part);
    });
  });

  return normalized.join("\n");
}

function balanceMarkdown(source) {
  const fenceCount = (source.match(/```/g) || []).length;
  return fenceCount % 2 === 1 ? `${source}\n\`\`\`` : source;
}

export function renderMarkdown(target, source) {
  const dirty = markdown.render(balanceMarkdown(normalizeMarkdown(source)));
  target.innerHTML = window.DOMPurify.sanitize(dirty);
  target.querySelectorAll("a[href]").forEach((link) => {
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
  });
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
