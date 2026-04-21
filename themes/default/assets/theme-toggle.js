(function () {
  const storageKey = "rookieblog-theme";
  const root = document.documentElement;
  const toggles = Array.from(document.querySelectorAll("[data-theme-toggle]"));
  const mediaQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function readStoredTheme() {
    try {
      const saved = window.localStorage.getItem(storageKey);
      return saved === "light" || saved === "dark" ? saved : "";
    } catch (error) {
      return "";
    }
  }

  function preferredTheme() {
    const saved = readStoredTheme();
    if (saved) {
      return saved;
    }
    return mediaQuery && mediaQuery.matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    const isDark = nextTheme === "dark";
    root.dataset.theme = nextTheme;
    toggles.forEach((toggle) => {
      toggle.setAttribute("aria-pressed", String(isDark));
      toggle.setAttribute("aria-label", isDark ? "切换到浅色模式" : "切换到暗色模式");
      toggle.setAttribute("title", isDark ? "切换到浅色模式" : "切换到暗色模式");
    });
  }

  function persistTheme(theme) {
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch (error) {
      return;
    }
  }

  applyTheme(preferredTheme());

  toggles.forEach((toggle) => {
    toggle.addEventListener("click", () => {
      const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
      persistTheme(nextTheme);
    });
  });

  if (mediaQuery) {
    const syncWithSystem = (event) => {
      if (readStoredTheme()) {
        return;
      }
      applyTheme(event.matches ? "dark" : "light");
    };
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", syncWithSystem);
    } else if (typeof mediaQuery.addListener === "function") {
      mediaQuery.addListener(syncWithSystem);
    }
  }
})();
