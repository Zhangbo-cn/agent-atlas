(function () {
  "use strict";

  var root = document.documentElement;

  function readTheme() {
    try {
      return localStorage.getItem("atlas-theme");
    } catch (e) {
      return null;
    }
  }

  function saveTheme(theme) {
    try {
      localStorage.setItem("atlas-theme", theme);
    } catch (e) {
      /* private mode or blocked storage */
    }
  }

  function effectiveTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit === "dark" || explicit === "light") return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function paintToggle() {
    var isDark = effectiveTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.setAttribute("aria-label", isDark ? "切换到浅色主题" : "切换到深色主题");
      button.setAttribute("title", isDark ? "切换到浅色主题" : "切换到深色主题");
      button.innerHTML = isDark
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>';
    });
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      saveTheme(next);
      paintToggle();
    });
  });

  if (window.matchMedia) {
    var media = window.matchMedia("(prefers-color-scheme: dark)");
    var onChange = function () {
      if (!root.getAttribute("data-theme")) paintToggle();
    };
    if (media.addEventListener) media.addEventListener("change", onChange);
    else if (media.addListener) media.addListener(onChange);
  }

  paintToggle();

  var bar = document.querySelector("[data-progress]");
  if (bar) {
    var update = function () {
      var max = document.documentElement.scrollHeight - window.innerHeight;
      var ratio = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
      bar.style.width = (ratio * 100).toFixed(2) + "%";
    };
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  }

  var tocLinks = Array.prototype.slice.call(document.querySelectorAll("[data-toc] a[href^='#']"));
  if (tocLinks.length) {
    var sections = tocLinks
      .map(function (link) {
        var id = link.getAttribute("href").slice(1);
        var el = id ? document.getElementById(id) : null;
        return el ? { link: link, el: el } : null;
      })
      .filter(Boolean);

    var setActive = function (id) {
      tocLinks.forEach(function (link) {
        link.classList.toggle("is-active", link.getAttribute("href") === "#" + id);
      });
    };

    if ("IntersectionObserver" in window) {
      var observer = new IntersectionObserver(
        function (entries) {
          var visible = entries
            .filter(function (entry) { return entry.isIntersecting; })
            .sort(function (a, b) { return a.boundingClientRect.top - b.boundingClientRect.top; });
          if (visible.length) setActive(visible[0].target.id);
        },
        { rootMargin: "-88px 0px -68% 0px", threshold: 0 }
      );
      sections.forEach(function (item) { observer.observe(item.el); });
    }

    tocLinks.forEach(function (link) {
      link.addEventListener("click", function () {
        var details = link.closest("details");
        if (details) details.open = false;
      });
    });
  }
})();
