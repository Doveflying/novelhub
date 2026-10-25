/* 书萤 · 全局脚本：主题、搜索快捷键、移动端抽屉、书架筛选 */
(function () {
  "use strict";

  var root = document.documentElement;
  var $ = function (sel, ctx) { return (ctx || document).querySelector(sel); };
  var $$ = function (sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); };
  var THEMES = ["paper", "sepia", "green", "night"];
  var THEME_BG = { paper: "#fff6f9", sepia: "#fff1e7", green: "#eefbf7", night: "#0c0d18" };

  function store(key, val) {
    try { localStorage.setItem(key, val); } catch (e) { /* ignore */ }
  }
  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }

  /* ---------------- 主题 ---------------- */
  function paintTheme(t) {
    root.setAttribute("data-theme", t);
    var use = $("#themeIcon use");
    if (use) use.setAttribute("href", t === "night" ? "#i-sun" : "#i-moon");
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", THEME_BG[t] || THEME_BG.paper);
    $$("[data-theme-choice]").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-theme-choice") === t);
    });
  }
  function setTheme(t) {
    if (THEMES.indexOf(t) < 0) t = "paper";
    paintTheme(t);
    store("nh-theme", t);
  }
  setTheme(read("nh-theme", "paper"));

  var themePop = $("#themePop");
  var themeBtn = $("#themeBtn");
  if (themeBtn && themePop) {
    themeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (themePop.classList.contains("on")) {
        themePop.classList.remove("on");
        return;
      }
      var r = themeBtn.getBoundingClientRect();
      themePop.classList.add("on");
      var w = themePop.offsetWidth;
      var left = Math.max(10, Math.min(r.right - w, window.innerWidth - w - 10));
      var top = r.bottom + 8;
      if (top + themePop.offsetHeight > window.innerHeight - 10) {
        top = Math.max(10, r.top - themePop.offsetHeight - 8);
      }
      themePop.style.left = left + "px";
      themePop.style.top = top + "px";
    });
    document.addEventListener("click", function (e) {
      if (!themePop.contains(e.target) && !themeBtn.contains(e.target)) {
        themePop.classList.remove("on");
      }
    });
  }
  $$("[data-theme-choice]").forEach(function (b) {
    b.addEventListener("click", function () {
      setTheme(b.getAttribute("data-theme-choice"));
      if (themePop) themePop.classList.remove("on");
      closeSheet();
    });
  });

  /* ---------------- 刷新 ---------------- */
  var refreshBtn = $("#refreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      refreshBtn.classList.add("spinning");
      location.reload();
    });
  }

  /* ---------------- 移动端「更多」 ---------------- */
  var sheet = $("#moreSheet");
  var scrim = $("#sheetScrim");
  var moreTab = $("#moreTab");

  function closeSheet() {
    if (!sheet) return;
    sheet.classList.remove("on");
    if (scrim) scrim.classList.remove("on");
    document.body.style.overflow = "";
  }

  if (moreTab && sheet) {
    moreTab.addEventListener("click", function () {
      if (sheet.classList.contains("on")) { closeSheet(); return; }
      sheet.classList.add("on");
      if (scrim) scrim.classList.add("on");
      document.body.style.overflow = "hidden";
    });
  }
  if (scrim) scrim.addEventListener("click", closeSheet);

  /* ---------------- 搜索快捷键 ---------------- */
  var search = $("#globalSearch");
  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    var typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (search) { search.focus(); search.select(); }
      return;
    }
    if (e.key === "/" && !typing && search) {
      e.preventDefault();
      search.focus();
    }
    if (e.key === "Escape") {
      if (themePop) themePop.classList.remove("on");
      closeSheet();
    }
  });

  /* ---------------- 书架：筛选 / 排序 / 视图 ---------------- */
  var grid = $("#bookGrid");
  if (grid) {
    var books = $$(".book", grid);
    var stateFilter = "all";
    var textFilter = "";
    var empty = $("#shelfEmpty");

    function applyFilter() {
      var shown = 0;
      books.forEach(function (b) {
        var okState = stateFilter === "all" || b.getAttribute("data-state") === stateFilter;
        var title = (b.getAttribute("data-title") || "").toLowerCase();
        var okText = !textFilter || title.indexOf(textFilter) >= 0;
        var ok = okState && okText;
        b.style.display = ok ? "" : "none";
        if (ok) shown += 1;
      });
      if (empty) empty.style.display = shown ? "none" : "";
      var counter = $("[data-visible-count]");
      if (counter) counter.textContent = shown;
    }

    $$("[data-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        stateFilter = btn.getAttribute("data-filter");
        $$("[data-filter]").forEach(function (b) {
          var on = b === btn;
          b.classList.toggle("on", on);
          b.setAttribute("aria-selected", on ? "true" : "false");
        });
        applyFilter();
      });
    });

    var filterInput = $("#shelfFilter");
    if (filterInput) {
      filterInput.addEventListener("input", function () {
        textFilter = this.value.trim().toLowerCase();
        applyFilter();
      });
    }

    var sortModes = [
      { key: "updated", label: "最近更新" },
      { key: "title", label: "书名" },
      { key: "progress", label: "进度" }
    ];
    var sortIndex = 0;
    var sortBtn = $("#sortBtn");
    if (sortBtn) {
      sortBtn.addEventListener("click", function () {
        sortIndex = (sortIndex + 1) % sortModes.length;
        var mode = sortModes[sortIndex];
        var label = $("[data-sort-label]", sortBtn);
        if (label) label.textContent = mode.label;
        var list = books.slice();
        if (mode.key === "title") {
          list.sort(function (a, b) {
            return (a.getAttribute("data-title") || "").localeCompare(b.getAttribute("data-title") || "", "zh");
          });
        } else {
          list.sort(function (a, b) {
            return Number(b.getAttribute("data-" + mode.key)) - Number(a.getAttribute("data-" + mode.key));
          });
        }
        list.forEach(function (b) { grid.insertBefore(b, empty || null); });
      });
    }

    var viewBtn = $("#viewBtn");
    if (viewBtn) {
      viewBtn.addEventListener("click", function () {
        var compact = grid.classList.toggle("compact");
        var use = viewBtn.querySelector("use");
        if (use) use.setAttribute("href", compact ? "#i-list" : "#i-grid");
      });
    }
  }

  /* ---------------- 历史：分类筛选 ---------------- */
  var histTabs = $("#histTabs");
  if (histTabs) {
    $$("button[data-kind]", histTabs).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-kind");
        $$("button[data-kind]", histTabs).forEach(function (b) {
          b.classList.toggle("on", b === btn);
        });
        $$(".hist").forEach(function (row) {
          row.style.display = kind === "all" || row.getAttribute("data-kind") === kind ? "" : "none";
        });
        $$("[data-group]").forEach(function (g) {
          var visible = $$(".hist", g).some(function (r) { return r.style.display !== "none"; });
          g.style.display = visible ? "" : "none";
        });
      });
    });
  }
})();
