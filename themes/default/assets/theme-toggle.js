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
    if (saved) return saved;
    return mediaQuery && mediaQuery.matches ? "dark" : "light";
  }

  function setToggleAttrs(isDark) {
    toggles.forEach((toggle) => {
      toggle.setAttribute("aria-pressed", String(isDark));
      toggle.setAttribute("aria-label", isDark ? "切换到浅色模式" : "切换到暗色模式");
      toggle.setAttribute("title", isDark ? "切换到浅色模式" : "切换到暗色模式");
    });
  }

  function applyTheme(theme, animate, originX, originY) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    const isDark = nextTheme === "dark";

    function doApply() {
      root.dataset.theme = nextTheme;
      setToggleAttrs(isDark);
    }

    if (!animate || !document.startViewTransition) {
      doApply();
      return;
    }

    const x = originX ?? 0;
    const y = originY ?? window.innerHeight;
    const maxR = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    ) + 20;

    // Disable CSS transitions so View Transition snapshots a clean frame
    const style = document.createElement("style");
    style.textContent = "*, *::before, *::after { transition: none !important; }";
    document.head.appendChild(style);

    const transition = document.startViewTransition(() => {
      doApply();
      // Force reflow so the snapshot captures the final state
      document.documentElement.offsetHeight;
    });

    transition.ready.then(() => {
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${maxR}px at ${x}px ${y}px)`] },
        {
          duration: 600,
          easing: "ease-in-out",
          pseudoElement: "::view-transition-new(root)",
        }
      );
    });

    transition.finished.then(() => {
      style.remove();
    });
  }

  function persistTheme(theme) {
    try {
      window.localStorage.setItem(storageKey, theme);
    } catch (error) {
      return;
    }
  }

  applyTheme(preferredTheme(), false);

  toggles.forEach((toggle) => {
    toggle.addEventListener("click", (e) => {
      const rect = toggle.getBoundingClientRect();
      const x = Math.round(rect.left + rect.width / 2);
      const y = Math.round(rect.top + rect.height / 2);
      const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(nextTheme, true, x, y);
      persistTheme(nextTheme);
    });
  });

  if (mediaQuery) {
    const syncWithSystem = (event) => {
      if (readStoredTheme()) return;
      applyTheme(event.matches ? "dark" : "light", false);
    };
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", syncWithSystem);
    } else if (typeof mediaQuery.addListener === "function") {
      mediaQuery.addListener(syncWithSystem);
    }
  }
})();
