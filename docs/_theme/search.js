(async function () {
  const root = document.querySelector("[data-search-root]");
  if (!root) return;

  const input = root.querySelector("[data-search-input]") || document.getElementById("global-search-input");
  const status = root.querySelector("[data-search-status]");
  const results = root.querySelector("[data-search-results]");
  const form = root.querySelector("[data-search-form]") || document.querySelector(".header-search");
  const clearButton = root.querySelector("[data-search-clear]");
  const heading = root.querySelector("[data-search-heading]");
  const indexUrl = root.getAttribute("data-search-index");

  if (!input || !form || !status || !results || !heading) return;

  const queryParams = new URLSearchParams(window.location.search);
  const initialQuery = queryParams.get("q") || "";

  const escapeHtml = (text) =>
    String(text || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  const highlight = (text, query) => {
    const safeText = escapeHtml(text);
    if (!query) return safeText;
    const pattern = new RegExp(`(${escapeRegExp(query)})`, "gi");
    return safeText.replace(pattern, "<mark>$1</mark>");
  };

  const snippetFor = (item, query) => {
    const content = item.content || item.summary || "";
    if (!query) return content.slice(0, 180);

    const normalized = content.toLowerCase();
    const index = normalized.indexOf(query.toLowerCase());
    if (index === -1) return content.slice(0, 180);

    const start = Math.max(0, index - 50);
    const end = Math.min(content.length, index + query.length + 90);
    const prefix = start > 0 ? "... " : "";
    const suffix = end < content.length ? " ..." : "";
    return `${prefix}${content.slice(start, end).trim()}${suffix}`;
  };

  const createResultCard = (item, query) => {
    const article = document.createElement("article");
    article.className = "search-result";

    const snippet = snippetFor(item, query);
    const tagsHtml = (item.tags || [])
      .map((tag) => `<span class="search-tag">${highlight(tag, query)}</span>`)
      .join("");

    article.innerHTML = `
      <div class="search-result__meta">
        <span class="search-kind">${item.kind === "page" ? "页面" : "文章"}</span>
        <span class="search-date">${item.date || "未设置日期"}</span>
      </div>
      <h3><a href="../${item.url}">${highlight(item.title, query)}</a></h3>
      <p class="search-result__summary">${highlight(item.summary || "", query)}</p>
      <p class="search-result__snippet">${highlight(snippet, query)}</p>
      <div class="search-result__footer">
        <div class="search-tag-row">${tagsHtml}</div>
        <span class="search-result__arrow">Read</span>
      </div>
    `;
    return article;
  };

  try {
    const response = await fetch(indexUrl);
    const items = await response.json();

    const render = (query) => {
      const normalized = query.trim().toLowerCase();
      results.innerHTML = "";
      heading.textContent = normalized ? `“${query}” 的搜索结果` : "全部内容";
      if (clearButton) {
        clearButton.hidden = !normalized;
      }

      const matches = (normalized ? items : items.slice())
        .map((item) => {
          const haystack = [item.title, item.summary, item.content, (item.tags || []).join(" ")].join(" ").toLowerCase();
          const score =
            (item.title || "").toLowerCase().includes(normalized) * 8 +
            (item.summary || "").toLowerCase().includes(normalized) * 4 +
            (normalized ? haystack.split(normalized).length - 1 : 1);
          return { item, score };
        })
        .filter((entry) => entry.score > 0)
        .sort((a, b) => {
          if (b.score !== a.score) return b.score - a.score;
          return (b.item.date || "").localeCompare(a.item.date || "");
        });

      if (!normalized) {
        status.textContent = `共收录 ${items.length} 篇内容，先看看最新文章，或直接输入关键词。`;
      } else {
        status.textContent = matches.length ? `找到 ${matches.length} 条与 “${query}” 相关的内容。` : `没有找到与 “${query}” 匹配的内容。`;
      }

      if (!matches.length) {
        results.innerHTML = `
          <article class="search-result search-result--empty">
            <div class="eyebrow">没有命中</div>
            <h3>试试换个关键词</h3>
            <p>你可以搜索标题、标签或正文中的词语，或者换一个更具体的关键词继续试试。</p>
          </article>
        `;
        return;
      }

      for (const { item } of matches) {
        results.appendChild(createResultCard(item, query));
      }
    };

    input.value = initialQuery;
    render(initialQuery);

    input.addEventListener("input", () => {
      const query = input.value.trim();
      const nextUrl = query ? `?q=${encodeURIComponent(query)}` : window.location.pathname;
      window.history.replaceState({}, "", nextUrl);
      render(query);
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = input.value.trim();
      const nextUrl = query ? `?q=${encodeURIComponent(query)}` : window.location.pathname;
      window.history.replaceState({}, "", nextUrl);
      render(query);
    });

    if (clearButton) {
      clearButton.addEventListener("click", () => {
        input.value = "";
        input.dispatchEvent(new Event("input"));
        input.focus();
      });
    }
  } catch (error) {
    status.textContent = "搜索索引加载失败，请重新构建站点后再试。";
    console.error(error);
  }
})();
