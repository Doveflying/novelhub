/* 轻库 · 原型交互
   仅用于演示：无后端，数据为静态示例。 */
(function () {
  "use strict";

  var root = document.documentElement;
  var body = document.body;
  var $ = function (sel, ctx) { return (ctx || document).querySelector(sel); };
  var $$ = function (sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); };

  var current = "shelf";
  var PLACEHOLDER = { comics: 1, anime: 1, crawl: 1, import: 1 };
  var TITLES = {
    shelf: "书架", detail: "书籍详情", reader: "阅读", history: "历史",
    search: "发现", sources: "书源", design: "设计规范",
    comics: "漫画", anime: "动漫", crawl: "抓取", import: "导入", placeholder: "漫画"
  };

  /* ---------------- toast ---------------- */
  var toastEl = $("#toast");
  var toastText = $("#toastText");
  var toastTimer = null;
  function toast(msg) {
    toastText.textContent = msg;
    toastEl.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("on"); }, 2600);
  }

  /* ---------------- theme ---------------- */
  function stored(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }
  function store(key, val) {
    try { localStorage.setItem(key, val); } catch (e) { /* file:// 下可能被禁用 */ }
  }
  function setTheme(t) {
    root.setAttribute("data-theme", t);
    store("nh-theme", t);
    $$("[data-set-theme]").forEach(function (b) {
      b.classList.toggle("on", b.getAttribute("data-set-theme") === t);
    });
    var use = $("#themeBtn use");
    if (use) use.setAttribute("href", t === "night" ? "#i-sun" : "#i-moon");
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t === "night" ? "#131519" : "#f6f2e9");
  }
  setTheme(stored("nh-theme", "paper"));

  /* ---------------- routing ---------------- */
  function go(name) {
    if (!name) return;
    var wasReader = current === "reader";
    current = name;
    try { history.replaceState(null, "", "#" + name); } catch (e) { /* file:// 可能不允许 */ }
    var viewName = PLACEHOLDER[name] ? "placeholder" : name;

    if (PLACEHOLDER[name]) {
      $("#phTitle").textContent = TITLES[name] || "页面";
      var desc = {
        comics: "漫画库沿用同一套网格与筛选：3:4 封面、状态点、继续阅读；点开进入纵向滚动阅读。",
        anime: "番剧库使用海报网格与选集列表，播放页为沉浸式播放器 + 弹幕层。",
        crawl: "抓取页：粘贴目录页 URL → 解析书名/分卷 → 后台任务实时进度，不阻塞页面。",
        import: "导入页：拖入 txt / epub / md，或填写磁盘路径批量导入。"
      };
      $("#phDesc").textContent = desc[name] || "这个页面在完整原型中沿用同一套外壳与组件规范。";
    }

    $$(".view").forEach(function (v) {
      v.classList.toggle("on", v.getAttribute("data-view") === viewName);
    });
    body.classList.toggle("reading", name === "reader");
    var navName = (name === "detail" || name === "reader") ? "shelf" : name;
    $$(".rail-link").forEach(function (a) { a.classList.toggle("on", a.getAttribute("data-go") === navName); });
    $$(".tab").forEach(function (t) { t.classList.toggle("on", t.getAttribute("data-go") === navName); });
    var crumb = $(".crumb b");
    if (crumb) crumb.textContent = TITLES[name] || "轻库";

    closeAll();
    window.scrollTo(0, 0);
    if (name === "reader") {
      requestAnimationFrame(updateReader);
      if (wasReader) toast("已切换章节（原型）");
    }
  }

  /* ---------------- overlay helpers ---------------- */
  var scrim = $("#scrim");
  var openDrawer = null;

  function lock(on) {
    body.style.overflow = on ? "hidden" : "";
  }
  function closePopovers() {
    $$(".popover").forEach(function (p) { p.classList.remove("on"); });
  }
  function closeDrawers() {
    $$(".drawer").forEach(function (d) { d.classList.remove("on"); });
    openDrawer = null;
  }
  function closeAll() {
    closeDrawers();
    closePopovers();
    $("#palette").classList.remove("on");
    $("#sheet").classList.remove("on");
    $("#modalWrap").classList.remove("on");
    scrim.classList.remove("on", "light");
    lock(false);
  }
  function openPanel(id) {
    closeDrawers();
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add("on");
    openDrawer = id;
    scrim.classList.toggle("light", id === "settingsDrawer");
    scrim.classList.add("on");
    lock(true);
  }
  scrim.addEventListener("click", function () {
    if (openDrawer) { closeDrawers(); scrim.classList.remove("on", "light"); lock(false); }
    else { closeAll(); }
  });
  $$("[data-close]").forEach(function (b) {
    b.addEventListener("click", function () { closeAll(); });
  });

  function placePop(pop, anchor, alignRight) {
    var r = anchor.getBoundingClientRect();
    pop.classList.add("on");
    var w = pop.offsetWidth;
    var h = pop.offsetHeight;
    var left = alignRight ? r.right - w : r.left;
    left = Math.max(10, Math.min(left, window.innerWidth - w - 10));
    var top = r.bottom + 8;
    if (top + h > window.innerHeight - 10) top = Math.max(10, r.top - h - 8);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  /* ---------------- theme popover ---------------- */
  var themePop = $("#themePop");
  $("#themeBtn").addEventListener("click", function (e) {
    e.stopPropagation();
    if (themePop.classList.contains("on")) { themePop.classList.remove("on"); return; }
    closePopovers();
    placePop(themePop, this, true);
  });

  /* ---------------- book context menu ---------------- */
  var bookPop = $("#bookMenuPop");
  var menuTitle = "";
  $$("[data-menu]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var book = btn.closest(".book");
      menuTitle = book ? (book.getAttribute("data-title") || "") : "这本书";
      var del = $("[data-confirm]", bookPop);
      if (del) del.setAttribute("data-confirm", "删除《" + menuTitle + "》及其全部章节？");
      closePopovers();
      placePop(bookPop, btn, true);
    });
  });

  /* ---------------- confirm modal ---------------- */
  var modalWrap = $("#modalWrap");
  var modalText = $("#modalText");
  var pendingConfirm = "";
  $$("[data-confirm]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      pendingConfirm = btn.getAttribute("data-confirm") || "";
      modalText.textContent = pendingConfirm;
      closePopovers();
      modalWrap.classList.add("on");
      lock(true);
    });
  });
  $("#modalOk").addEventListener("click", function () {
    modalWrap.classList.remove("on");
    lock(false);
    toast(pendingConfirm.indexOf("清空") >= 0 ? "历史已清空（原型）" : "已删除（原型）");
  });

  /* ---------------- global click ---------------- */
  document.addEventListener("click", function (e) {
    var goEl = e.target.closest("[data-go]");
    if (goEl) {
      e.preventDefault();
      go(goEl.getAttribute("data-go"));
      return;
    }
    var toastEl2 = e.target.closest("[data-toast]");
    if (toastEl2) {
      e.preventDefault();
      toast(toastEl2.getAttribute("data-toast"));
      closePopovers();
      return;
    }
    var themeBtn = e.target.closest("[data-set-theme]");
    if (themeBtn) {
      setTheme(themeBtn.getAttribute("data-set-theme"));
      closePopovers();
      return;
    }
    if (!e.target.closest(".popover") && !e.target.closest("#themeBtn") && !e.target.closest("[data-menu]")) {
      closePopovers();
    }
  });

  /* ---------------- command palette ---------------- */
  var palette = $("#palette");
  var pInput = $("#paletteInput");
  var pList = $("#paletteList");
  var pItems = $$(".palette-item", pList);
  var pIndex = 0;

  function openPalette() {
    closeAll();
    palette.classList.add("on");
    lock(true);
    pInput.value = "";
    pItems.forEach(function (i) { i.style.display = ""; });
    $$(".palette-label", pList).forEach(function (l) { l.style.display = ""; });
    pIndex = 0;
    highlight();
    setTimeout(function () { pInput.focus(); }, 30);
  }
  function closePalette() {
    palette.classList.remove("on");
    lock(false);
  }
  function visibleItems() {
    return pItems.filter(function (i) { return i.style.display !== "none"; });
  }
  function highlight() {
    var vis = visibleItems();
    pItems.forEach(function (i) { i.classList.remove("sel"); });
    if (vis[pIndex]) vis[pIndex].classList.add("sel");
  }
  function filterPalette() {
    var q = pInput.value.trim().toLowerCase();
    pItems.forEach(function (i) {
      i.style.display = !q || i.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none";
    });
    $$(".palette-label", pList).forEach(function (l) { l.style.display = q ? "none" : ""; });
    pIndex = 0;
    highlight();
  }
  pInput.addEventListener("input", filterPalette);
  pInput.addEventListener("keydown", function (e) {
    var vis = visibleItems();
    if (e.key === "ArrowDown") { e.preventDefault(); pIndex = Math.min(vis.length - 1, pIndex + 1); highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); pIndex = Math.max(0, pIndex - 1); highlight(); }
    else if (e.key === "Enter") { e.preventDefault(); if (vis[pIndex]) vis[pIndex].click(); }
  });
  pItems.forEach(function (item) {
    item.addEventListener("mouseenter", function () {
      pIndex = visibleItems().indexOf(item);
      highlight();
    });
  });
  palette.addEventListener("click", function (e) { if (e.target === palette) closePalette(); });

  /* ---------------- shelf filters / sort / view ---------------- */
  var bookGrid = $("#bookGrid");
  var books = $$(".book", bookGrid);
  var stateFilter = "all";
  var textFilter = "";

  var emptyBox = document.createElement("div");
  emptyBox.className = "empty";
  emptyBox.style.cssText = "display:none;grid-column:1/-1;padding:36px 20px";
  emptyBox.innerHTML = '<p class="big" style="font-size:20px">没有匹配的书</p><p>换个筛选条件，或清空搜索词试试。</p>';
  bookGrid.appendChild(emptyBox);

  function applyShelfFilter() {
    var shown = 0;
    books.forEach(function (b) {
      var okState = stateFilter === "all" || b.getAttribute("data-state") === stateFilter;
      var okText = !textFilter || (b.getAttribute("data-title") || "").toLowerCase().indexOf(textFilter) >= 0;
      var ok = okState && okText;
      b.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    emptyBox.style.display = shown ? "none" : "";
  }
  $$("[data-filter]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      stateFilter = btn.getAttribute("data-filter");
      $$("[data-filter]").forEach(function (b) {
        var on = b === btn;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      applyShelfFilter();
    });
  });
  $("#shelfFilter").addEventListener("input", function () {
    textFilter = this.value.trim().toLowerCase();
    applyShelfFilter();
  });

  var sortModes = [
    { key: "updated", label: "最近更新" },
    { key: "title", label: "书名" },
    { key: "progress", label: "进度" }
  ];
  var sortIndex = 0;
  $("#sortBtn").addEventListener("click", function () {
    sortIndex = (sortIndex + 1) % sortModes.length;
    var mode = sortModes[sortIndex];
    $("span", this).textContent = mode.label;
    var list = books.slice();
    if (mode.key === "title") {
      list.sort(function (a, b) { return (a.getAttribute("data-title") || "").localeCompare(b.getAttribute("data-title") || "", "zh"); });
    } else {
      list.sort(function (a, b) {
        return Number(b.getAttribute("data-" + mode.key)) - Number(a.getAttribute("data-" + mode.key));
      });
    }
    list.forEach(function (b) { bookGrid.insertBefore(b, emptyBox); });
    toast("已按「" + mode.label + "」排序");
  });

  $("#viewBtn").addEventListener("click", function () {
    var compact = bookGrid.classList.toggle("compact");
    this.querySelector("use").setAttribute("href", compact ? "#i-list" : "#i-grid");
    toast(compact ? "已切换为紧凑视图" : "已切换为标准视图");
  });

  /* ---------------- history filter ---------------- */
  $$("#histTabs button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var kind = btn.getAttribute("data-kind");
      $$("#histTabs button").forEach(function (b) { b.classList.toggle("on", b === btn); });
      $$(".hist").forEach(function (row) {
        row.style.display = kind === "all" || row.getAttribute("data-kind") === kind ? "" : "none";
      });
      $$("[data-group]").forEach(function (g) {
        var visible = $$(".hist", g).some(function (r) { return r.style.display !== "none"; });
        g.style.display = visible ? "" : "none";
      });
    });
  });

  /* ---------------- reading settings ---------------- */
  var fs = Number(stored("nh-fs", 19));
  var lh = Number(stored("nh-lh", 195));
  var measure = Number(stored("nh-measure", 38));
  var readingFont = stored("nh-font", "serif");

  function applyReading() {
    root.style.setProperty("--reading-size", fs + "px");
    root.style.setProperty("--reading-lh", (lh / 100).toFixed(2));
    root.style.setProperty("--measure", measure + "em");
    root.style.setProperty("--reading-font", readingFont === "sans" ? "var(--font-sans)" : "var(--font-serif)");
    $("#fsOut").textContent = fs + " px";
    $("#lhOut").textContent = (lh / 100).toFixed(2);
    $("#measureOut").textContent = measure + " em";
    $("#lhRange").value = lh;
    $("#measureRange").value = measure;
    $$("[data-font]").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-font") === readingFont); });
    store("nh-fs", fs); store("nh-lh", lh); store("nh-measure", measure); store("nh-font", readingFont);
  }
  $("#fsMinus").addEventListener("click", function () { fs = Math.max(15, fs - 1); applyReading(); });
  $("#fsPlus").addEventListener("click", function () { fs = Math.min(30, fs + 1); applyReading(); });
  $("#lhRange").addEventListener("input", function () { lh = Number(this.value); applyReading(); });
  $("#measureRange").addEventListener("input", function () { measure = Number(this.value); applyReading(); });
  $$("[data-font]").forEach(function (b) {
    b.addEventListener("click", function () { readingFont = b.getAttribute("data-font"); applyReading(); });
  });
  $("#resetReading").addEventListener("click", function () {
    fs = 19; lh = 195; measure = 38; readingFont = "serif";
    applyReading();
    toast("阅读设置已恢复默认");
  });
  applyReading();

  /* ---------------- reader scroll ---------------- */
  var readerLine = $("#readerLine");
  var rtPct = $("#rtPct");
  var readerTop = $("#readerTop");
  var readerDock = $("#readerDock");
  var lastY = 0;
  var ticking = false;

  function updateReader() {
    if (current !== "reader") return;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    var p = max > 0 ? Math.round((window.scrollY / max) * 100) : 0;
    p = Math.max(1, Math.min(100, p));
    readerLine.style.width = p + "%";
    if (rtPct) rtPct.textContent = p + "%";
  }

  window.addEventListener("scroll", function () {
    if (current !== "reader") return;
    if (!ticking) {
      requestAnimationFrame(function () { updateReader(); ticking = false; });
      ticking = true;
    }
    var y = window.scrollY;
    var delta = y - lastY;
    if (y > 180 && delta > 6) {
      readerTop.classList.add("hide");
      readerDock.classList.add("hide");
    } else if (delta < -6 || y < 120) {
      readerTop.classList.remove("hide");
      readerDock.classList.remove("hide");
    }
    lastY = y;
  }, { passive: true });

  document.addEventListener("mousemove", function (e) {
    if (current === "reader" && e.clientY < 70) {
      readerTop.classList.remove("hide");
      readerDock.classList.remove("hide");
    }
  });

  /* ---------------- openers ---------------- */
  $$("[data-open]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      var id = btn.getAttribute("data-open") === "toc" ? "tocDrawer" : "settingsDrawer";
      openPanel(id);
    });
  });

  $("#bookmarkBtn").addEventListener("click", function () {
    this.classList.toggle("on");
    toast(this.classList.contains("on") ? "已添加书签" : "已移除书签");
  });

  /* ---------------- detail: 简介展开 / 分卷折叠 ---------------- */
  var descToggle = $("#descToggle");
  if (descToggle) {
    descToggle.addEventListener("click", function () {
      var open = $("#detailDesc").classList.toggle("open");
      this.textContent = open ? "收起简介" : "展开简介";
    });
  }
  $$(".vol-head .fold").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var list = btn.closest(".vol-head").nextElementSibling;
      if (!list || !list.classList.contains("chapters")) return;
      var hidden = list.style.display === "none";
      list.style.display = hidden ? "" : "none";
      btn.style.transform = hidden ? "" : "rotate(-90deg)";
    });
  });

  window.addEventListener("resize", updateReader);

  /* ---------------- global search ---------------- */
  $("#globalSearch").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      var v = this.value.trim();
      $("#bigSearch").value = v;
      go("search");
      if (v) toast("正在搜索「" + v + "」");
    }
  });
  $("#bigSearch").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      toast("正在搜索「" + this.value.trim() + "」");
    }
  });

  /* ---------------- mobile more sheet ---------------- */
  $("#moreTab").addEventListener("click", function () {
    var sheet = $("#sheet");
    if (sheet.classList.contains("on")) { closeAll(); return; }
    closeAll();
    sheet.classList.add("on");
    scrim.classList.add("on");
    lock(true);
  });

  /* ---------------- keyboard ---------------- */
  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    var typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;

    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.key === "Escape") {
      closeAll();
      return;
    }
    if (typing) return;

    if (e.key === "/") {
      e.preventDefault();
      $("#globalSearch").focus();
      return;
    }
    if (current === "reader") {
      if (e.key === "ArrowLeft") { e.preventDefault(); window.scrollTo(0, 0); toast("上一章（原型）"); }
      if (e.key === "ArrowRight") { e.preventDefault(); window.scrollTo(0, 0); toast("下一章 · 已缓存（原型）"); }
      if (e.key.toLowerCase() === "t") { e.preventDefault(); openPanel("tocDrawer"); }
      if (e.key.toLowerCase() === "s") { e.preventDefault(); openPanel("settingsDrawer"); }
    }
  });

  /* ---------------- init ---------------- */
  var initial = (location.hash || "").replace("#", "");
  if (initial && (TITLES[initial] || PLACEHOLDER[initial])) {
    body.classList.add("no-anim");
    go(initial);
    setTimeout(function () { body.classList.remove("no-anim"); }, 400);
  }
  updateReader();
})();
