/* 书萤 · 二次元角色系统
   - 页面感知的看板娘（每个页面默认角色不同）
   - 点击换台词 / 气泡 / 关闭记忆
   - 角色图鉴：常驻角色、跟随页面、显示开关
   所有状态存 localStorage，不依赖后端。 */
(function () {
  "use strict";

  var root = document.documentElement;
  var body = document.body;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  function store(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function read(k, f) { try { return localStorage.getItem(k) || f; } catch (e) { return f; } }

  /* ---------------- 角色资料 ---------------- */
  var CAST = {
    shiori: {
      name: "栞",
      jp: "シオリ",
      role: "图书委员",
      feature: "书架 / 目录 / 阅读进度",
      color: "#7C6BFF",
      lines: {
        index: [
          "欢迎回来～今天想从哪一本开始？",
          "书架我整理好了，最新的都排在最前面哦。",
          "「继续阅读」那一排，是你上次停下的地方。",
          "读累了就休息一下，书不会跑掉的。"
        ],
        book: [
          "目录按卷排好了，慢慢挑一章吧。",
          "进度我都记着，翻到哪页都不会丢。",
          "这本的简介有点长，点「展开全部」就能看完。"
        ],
        history: [
          "这几天的阅读记录都在这儿，像书签一样。",
          "昨天读到深夜的那本，今天还继续吗？"
        ],
        search: [
          "想找什么？书名、作者、正文片段都能搜。",
          "本地没有的话，我再去在线书源翻翻。"
        ],
        default: [
          "需要帮忙的话，点一下我就好。",
          "书萤的每一本书，我都记得放在哪一格。"
        ]
      }
    },
    miku: {
      name: "美玖",
      jp: "ミク",
      role: "追番担当",
      feature: "番剧收藏 / 播放 / 弹幕",
      color: "#FF5C8D",
      lines: {
        animes: [
          "新番都追到第几集啦？别攒着不看哦！",
          "耳机戴好，今晚就交给这一部了！",
          "收藏的番我都按更新时间排好了～",
          "片头曲千万别跳，那是灵魂！"
        ],
        default: [
          "这一集看完记得点「继续观看」，我帮你记着。",
          "弹幕开了吗？热闹一点才像追番嘛。"
        ]
      }
    },
    rin: {
      name: "凛",
      jp: "リン",
      role: "漫画担当",
      feature: "漫画书架 / 章节 / 跨页",
      color: "#2FC8B8",
      lines: {
        comics: [
          "分镜翻页的时候，别忘了看看跨页彩图～",
          "漫画书架空着的话，去聚合搜索捞一部回来。",
          "读到第几话我都记着，接着看就行。"
        ],
        default: [
          "格子与格子之间，藏着作者的小心思哦。",
          "这一话的分镜很漂亮，慢一点翻。"
        ]
      }
    },
    yuki: {
      name: "小雪",
      jp: "ユキ",
      role: "伴读担当",
      feature: "沉浸阅读 / 字号 / 主题",
      color: "#FFA43D",
      lines: {
        read: [
          "灯光调好了，慢慢读，我在这儿陪着。",
          "这一章有点长，记得眨眨眼。",
          "字号不合适的话，点右上角的 Aa 调一下。",
          "读到「本章完」的时候，记得伸个懒腰。"
        ],
        default: [
          "夜深了，把主题切成「深夜霓虹」会更舒服。",
          "给你温了一杯热可可，读完这章再喝。"
        ]
      }
    },
    aju: {
      name: "小蛛",
      jp: "アジュ",
      role: "书源担当",
      feature: "抓取 / 导入 / 书源管理",
      color: "#4F7BFF",
      lines: {
        crawl: [
          "抓取任务在跑，先喝口水吧。",
          "整本抓取会慢一点，进度条走完就好。",
          "章节缺了的话，用「补抓未完成章节」就行。"
        ],
        import: [
          "txt / epub 直接拖进来，我帮你拆章节。",
          "文件大一点也没关系，慢慢等就好。"
        ],
        sources: [
          "书源我都帮你盯着，挂了会亮红点的。",
          "新源装上以后，记得回搜索页勾选。"
        ],
        default: [
          "网络的事交给我，你只管看。",
          "源站偶尔抽风，重试一下通常就好了。"
        ]
      }
    },
    shirakaba: {
      name: "白桦",
      jp: "シラカバ",
      role: "馆长",
      feature: "历史 / 档案 / 统计",
      color: "#3E5C9A",
      lines: {
        history: [
          "这几天的阅读记录都归档好了，按日期排的。",
          "昨天读到深夜的那本，今天还继续吗？",
          "档案不会丢，你放心往后翻。"
        ],
        default: [
          "统计报表我放在抽屉里，需要的话说一声。",
          "书库的账，我替你记着。"
        ]
      }
    }
  };

  var ORDER = ["shiori", "miku", "rin", "yuki", "aju", "shirakaba"];
  var PAGE_CAST = {
    read: "yuki",
    comics: "rin", comic_detail: "rin", comic_read: "rin",
    animes: "miku", anime_detail: "miku", anime_play: "miku",
    crawl_page: "aju", task_page: "aju", import_page: "aju",
    sources_page: "aju", sources_rank: "aju",
    history: "shirakaba"
  };
  var CONTEXT = {
    read: "read", book: "book", index: "index", history: "history", search: "search",
    comics: "comics", comic_detail: "comics", comic_read: "comics",
    animes: "animes", anime_detail: "animes", anime_play: "animes",
    crawl_page: "crawl", task_page: "crawl", import_page: "import",
    sources_page: "sources", sources_rank: "sources"
  };

  var page = body.getAttribute("data-page") || "";
  // PAGE_CAST 是前端兜底表；后端给了 data-cast（例如聊天页按 who 指定）就以它为准
  var pageCast = PAGE_CAST[page] || body.getAttribute("data-cast") || "shiori";
  var ctx = CONTEXT[page] || "default";

  /* 偏好：auto = 跟随页面；否则是常驻角色 id */
  var fav = read("nh-cast", "auto");
  if (fav !== "auto" && !CAST[fav]) fav = "auto";
  var mascotOn = read("nh-mascot", "on") !== "off";

  function activeCast() { return fav === "auto" ? pageCast : fav; }

  function pickLine(id) {
    var c = CAST[id];
    if (!c) return "";
    var pool = (c.lines && c.lines[ctx]) || (c.lines && c.lines.default) || ["……"];
    return pool[Math.floor(Math.random() * pool.length)];
  }

  /* ---------------- 元素 ---------------- */
  var mascot = $("#mascot");
  var bubble = $("#mascotBubble");
  var bubbleLine = $("#mascotLine");
  var bubbleName = $("#mascotName");
  var mascotImg = $("#mascotImg");
  var modal = $("#castModal");
  var rail = $("#castRail");
  var railImg = $("#castRailImg");
  var railName = $("#castRailName");
  var railRole = $("#castRailRole");
  var autoBtn = $("#castAuto");
  var showBtn = $("#castShow");
  var nowLabel = $("#castNow");
  var lineIndex = 0;
  var bubbleTimer = null;
  var lastFocus = null;

  var FACE_BASE = (function () {
    var src = (mascotImg && mascotImg.getAttribute("src")) || "/static/cast/shiori-face.jpg";
    return src.replace(/[^/]+$/, "");
  })();

  function faceSrc(id) { return FACE_BASE + id + "-face.jpg"; }

  function paintChrome() {
    var id = activeCast();
    var c = CAST[id];
    if (mascotImg) { mascotImg.src = faceSrc(id); mascotImg.alt = c.name; }
    if (railImg) { railImg.src = faceSrc(id); railImg.alt = c.name; }
    if (railName) railName.textContent = c.name;
    if (railRole) railRole.textContent = c.role;
    if (rail) rail.setAttribute("data-cast", id);
    if (mascot) mascot.setAttribute("data-cast", id);
    if (bubbleName) bubbleName.textContent = c.name + " · " + c.role;

    $$("[data-cast-pick]", modal).forEach(function (card) {
      card.setAttribute("data-on", fav !== "auto" && card.getAttribute("data-cast-pick") === fav ? "1" : "0");
    });
    if (autoBtn) autoBtn.setAttribute("aria-pressed", fav === "auto" ? "true" : "false");
    if (showBtn) showBtn.setAttribute("aria-pressed", mascotOn ? "true" : "false");
    if (nowLabel) {
      nowLabel.textContent = fav === "auto"
        ? "跟随页面 · " + CAST[pageCast].name
        : CAST[fav].name + " · " + CAST[fav].role;
    }
  }

  function showBubble(text, autoHide) {
    if (!bubble || !mascotOn) return;
    if (bubbleLine) bubbleLine.textContent = text || pickLine(activeCast());
    bubble.classList.add("on");
    if (mascot) mascot.classList.add("on");
    clearTimeout(bubbleTimer);
    if (autoHide) {
      bubbleTimer = setTimeout(function () { bubble.classList.remove("on"); }, 9000);
    }
  }

  function hideBubble() {
    clearTimeout(bubbleTimer);
    if (bubble) bubble.classList.remove("on");
  }

  function popMascot() {
    if (!mascot) return;
    mascot.classList.remove("pop");
    void mascot.offsetWidth;
    mascot.classList.add("pop");
    setTimeout(function () { mascot.classList.remove("pop"); }, 500);
  }

  /* ---------------- 看板娘 ---------------- */
  if (mascot) {
    if (mascotOn) {
      setTimeout(function () { mascot.classList.add("on"); }, 600);
      var wide = window.matchMedia("(min-width: 761px)").matches;
      if (wide && !body.classList.contains("reading")) {
        setTimeout(function () { showBubble(pickLine(activeCast()), true); }, 1500);
      }
    }

    var face = $("#mascotFace");
    if (face) {
      face.addEventListener("click", function () {
        lineIndex += 1;
        var c = CAST[activeCast()];
        var pool = (c.lines && c.lines[ctx]) || c.lines.default;
        var line = pool[lineIndex % pool.length];
        showBubble(line, false);
        popMascot();
      });
    }
    var close = $("#mascotClose");
    if (close) {
      close.addEventListener("click", function (e) {
        e.stopPropagation();
        hideBubble();
      });
    }
  }

  /* ---------------- 图鉴弹窗 ---------------- */
  function openModal() {
    if (!modal) return;
    lastFocus = document.activeElement;
    paintChrome();
    modal.classList.add("on");
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    var x = $(".cast-close", modal);
    if (x) x.focus();
  }
  function closeModal() {
    if (!modal) return;
    modal.classList.remove("on");
    modal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  $$("[data-cast-open]").forEach(function (b) {
    b.addEventListener("click", openModal);
  });
  if (modal) {
    var scrim = $(".cast-scrim", modal);
    if (scrim) scrim.addEventListener("click", closeModal);
    $$(".cast-close", modal).forEach(function (b) { b.addEventListener("click", closeModal); });
    $$("[data-cast-pick]", modal).forEach(function (card) {
      card.addEventListener("click", function () {
        var id = card.getAttribute("data-cast-pick");
        fav = id;
        store("nh-cast", id);
        mascotOn = true;
        store("nh-mascot", "on");
        paintChrome();
        showBubble(pickLine(id), true);
        popMascot();
      });
    });
  }
  if (autoBtn) {
    autoBtn.addEventListener("click", function () {
      fav = "auto";
      store("nh-cast", "auto");
      paintChrome();
      showBubble(pickLine(activeCast()), true);
    });
  }
  if (showBtn) {
    showBtn.addEventListener("click", function () {
      mascotOn = !mascotOn;
      store("nh-mascot", mascotOn ? "on" : "off");
      if (mascot) mascot.classList.toggle("on", mascotOn);
      if (!mascotOn) hideBubble();
      paintChrome();
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal && modal.classList.contains("on")) closeModal();
  });

  /* ---------------- 看板娘可拖动 ----------------
   *
   * 需求：那个圆圈能拖到任意位置，松手后记住，下次打开还在原地。
   * 实现要点：
   *   - 用 left/top 绝对定位（CSS 里默认 right/bottom，拖动时切过去）
   *   - 指针事件统一走 pointerdown/move/up，鼠标和触摸一套代码
   *   - 拖过之后要给「点击」加个闸，否则松手会误触发换台词
   *   - 吸附到左右边缘（贴边 8px），并夹在视口内，别拖出屏幕
   *   - 位置存 localStorage，窗口缩放时重新夹一遍
   */
  (function initMascotDrag() {
    if (!mascot) return;
    var face = $("#mascotFace") || mascot.querySelector(".mascot-face");
    if (!face) return;

    var POS_KEY = "nh-mascot-pos";
    var EDGE = 8;              // 贴边留白
    var dragging = false;
    var moved = false;         // 这次按下有没有真的移动过
    var startX = 0, startY = 0;
    var originX = 0, originY = 0;
    var raf = null;

    function viewport() {
      return { w: window.innerWidth, h: window.innerHeight };
    }

    function clamp(x, y) {
      var vp = viewport();
      var w = mascot.offsetWidth || 66;
      var h = mascot.offsetHeight || 66;
      var maxX = Math.max(EDGE, vp.w - w - EDGE);
      var maxY = Math.max(EDGE, vp.h - h - EDGE);
      return {
        x: Math.min(Math.max(x, EDGE), maxX),
        y: Math.min(Math.max(y, EDGE), maxY)
      };
    }

    // 把 right/bottom 定位换算成 left/top，之后一律用 left/top
    function toLeftTop() {
      var r = mascot.getBoundingClientRect();
      mascot.style.left = r.left + "px";
      mascot.style.top = r.top + "px";
      mascot.style.right = "auto";
      mascot.style.bottom = "auto";
      return { x: r.left, y: r.top };
    }

    function apply(x, y) {
      var p = clamp(x, y);
      mascot.style.left = p.x + "px";
      mascot.style.top = p.y + "px";
      mascot.style.right = "auto";
      mascot.style.bottom = "auto";
      return p;
    }

    function save(x, y) {
      try { localStorage.setItem(POS_KEY, JSON.stringify({ x: x, y: y })); } catch (e) {}
    }

    function restore() {
      var raw = null;
      try { raw = localStorage.getItem(POS_KEY); } catch (e) {}
      if (!raw) return false;
      var p;
      try { p = JSON.parse(raw); } catch (e) { return false; }
      if (typeof p.x !== "number" || typeof p.y !== "number") return false;
      var c = clamp(p.x, p.y);
      mascot.style.left = c.x + "px";
      mascot.style.top = c.y + "px";
      mascot.style.right = "auto";
      mascot.style.bottom = "auto";
      return true;
    }

    function onDown(e) {
      // 只处理主键 / 单指
      if (e.button !== undefined && e.button !== 0) return;
      var start = toLeftTop();
      dragging = true;
      moved = false;
      startX = e.clientX;
      startY = e.clientY;
      originX = start.x;
      originY = start.y;
      mascot.classList.add("dragging");
      try { face.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    }

    function onMove(e) {
      if (!dragging) return;
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      if (!moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) moved = true;
      if (!moved) return;
      var x = originX + dx;
      var y = originY + dy;
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () { apply(x, y); });
    }

    function onUp(e) {
      if (!dragging) return;
      dragging = false;
      mascot.classList.remove("dragging");
      try { face.releasePointerCapture(e.pointerId); } catch (err) {}
      // 取消还在排队的 rAF，否则它会在吸附之后再把位置改回拖动中的值
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      if (!moved) return;   // 没移动就当点击，交给原来的点击逻辑

      // 注意：不能用 getBoundingClientRect() 读当前位置 —— apply() 走的是
      // requestAnimationFrame，这里读到的可能还是拖动前的旧值。
      // 直接用「起点 + 指针位移」算出落点，再吸附到更近的一侧。
      var finalX = originX + (e.clientX - startX);
      var finalY = originY + (e.clientY - startY);
      var p0 = clamp(finalX, finalY);
      var vp = viewport();
      var w = mascot.offsetWidth || 66;
      var snapX = (p0.x + w / 2) < vp.w / 2 ? EDGE : vp.w - w - EDGE;
      var p = apply(snapX, p0.y);
      save(p.x, p.y);
    }

    // 拖过之后，拦掉紧跟的一次 click，避免误触发「换句话」
    function onClickCapture(e) {
      if (moved) {
        e.stopPropagation();
        e.preventDefault();
        moved = false;
      }
    }

    face.addEventListener("pointerdown", onDown);
    face.addEventListener("pointermove", onMove);
    face.addEventListener("pointerup", onUp);
    face.addEventListener("pointercancel", onUp);
    face.addEventListener("click", onClickCapture, true);
    // 拖动时不要选中文字 / 触发页面滚动
    face.style.touchAction = "none";

    window.addEventListener("resize", function () {
      if (!localStorage) return;
      var r = mascot.getBoundingClientRect();
      apply(r.left, r.top);
    });

    restore();
  })();

  paintChrome();
})();
