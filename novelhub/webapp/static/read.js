/* 阅读器：显示设置、进度记忆、键盘翻章 */
(function () {
  var reader = document.getElementById('reader');
  if (!reader) return;
  var bookId = reader.dataset.book;
  var idx = parseInt(reader.dataset.idx, 10);
  var root = document.documentElement;
  var settings = document.getElementById('settingsPop');
  var drawer = document.getElementById('tocDrawer');

  /* ---------- 插图：逐张占位 + 计数 + 失败重试 ---------- */
  var Illus = (function () {
    var total = 0, ok = 0, fail = 0, badge = null;

    function ensureBadge() {
      if (badge) return badge;
      var meta = document.querySelector('.chapter-meta');
      if (!meta) return null;
      badge = document.createElement('span');
      badge.className = 'badge illus-badge';
      meta.appendChild(document.createTextNode(' · '));
      meta.appendChild(badge);
      return badge;
    }

    function paint() {
      var b = ensureBadge();
      if (!b) return;
      if (!total) { b.style.display = 'none'; return; }
      b.style.display = '';
      var s = '插图 ' + total + ' 张 · 已加载 ' + ok;
      if (fail) s += ' · 失败 ' + fail;
      b.textContent = s + (ok + fail < total ? ' · 加载中…' : '');
      b.classList.toggle('done', ok + fail >= total);
    }

    function retry(box, img) {
      box.className = 'illus-box loading';
      var ph = box.querySelector('.illus-ph');
      if (ph) ph.innerHTML = '<span class="illus-spin"></span>重新加载中…';
      var src = img.getAttribute('src') || '';
      var base = src.split(/[&?]r=\d+/)[0];
      img.src = base + (base.indexOf('?') >= 0 ? '&' : '?') + 'r=' + Date.now();
    }

    function wire(box, img) {
      function good() {
        box.className = 'illus-box ok';
        var ph = box.querySelector('.illus-ph');
        if (ph && ph.parentNode) ph.parentNode.removeChild(ph);
        ok += 1;
        paint();
      }
      function bad() {
        if (box.dataset.failed) return;
        box.dataset.failed = '1';
        box.className = 'illus-box fail';
        var ph = box.querySelector('.illus-ph');
        if (ph) ph.innerHTML = '插图加载失败 · 点这里重试';
        fail += 1;
        paint();
      }
      img.addEventListener('load', good);
      img.addEventListener('error', bad);
      box.addEventListener('click', function () {
        if (box.classList.contains('fail')) {
          box.dataset.failed = '';
          fail = Math.max(0, fail - 1);
          retry(box, img);
        }
      });
      if (img.complete && img.naturalWidth > 0) good();
    }

    function scan(scope) {
      var imgs = (scope || document).querySelectorAll('.chapter-body img');
      Array.prototype.forEach.call(imgs, function (img) {
        if (img.dataset.illusInit) return;
        img.dataset.illusInit = '1';
        if (!img.parentNode) return;
        var n = total + 1;
        var box = document.createElement('span');
        box.className = 'illus-box loading';
        img.parentNode.insertBefore(box, img);
        box.appendChild(img);
        var ph = document.createElement('span');
        ph.className = 'illus-ph';
        ph.innerHTML = '<span class="illus-spin"></span>插图 ' + n + ' 加载中…';
        box.appendChild(ph);
        total = n;
        paint();
        wire(box, img);
      });
      paint();
    }

    function reset() {
      total = ok = fail = 0;
      paint();
    }

    return { scan: scan, reset: reset, paint: paint };
  })();

  Illus.scan(document);

  /* ---------- 在线读取：边抓边加载（SSE），失败先自动重试，再给手动重载 ---------- */
  if (reader.dataset.live === '1') {
    var body = document.getElementById('chapterBody');
    var wc = document.getElementById('wordCount');
    var skel = document.getElementById('liveSkeleton');
    var done = false;
    var gotAny = false;
    var words = 0;
    var es = null;
    var tries = 0;
    var MAX_TRIES = 2;

    function addBadge() {
      var meta = document.querySelector('.chapter-meta');
      if (meta && !meta.querySelector('.badge.live')) {
        var b = document.createElement('span');
        b.className = 'badge live';
        b.textContent = '在线读取 · 未落库';
        meta.appendChild(document.createTextNode(' · '));
        meta.appendChild(b);
      }
    }

    function append(html) {
      if (skel && skel.parentNode) skel.parentNode.removeChild(skel);
      gotAny = true;
      body.insertAdjacentHTML('beforeend', html || '');
      Illus.scan(body);          /* 每来一段就登记新插图（占位+计数） */
    }

    /* 失败界面：先给"重新加载"，再说可能的原因 */
    function showError(msg, canRetry) {
      if (skel && skel.parentNode) skel.parentNode.removeChild(skel);
      var extra = canRetry ? '' :
        '<p class="hint">这一章可能源站没有、需要登录，或这一话被删除。可以换源或直接打开源站。</p>';
      body.insertAdjacentHTML('beforeend',
        '<div class="load-fail">' +
        (msg ? '<p class="sk-error">' + msg + '</p>' : '') +
        extra +
        '<p class="fail-actions">' +
        (canRetry ? '<button class="btn sm" id="retryBtn">重新加载</button>' : '') +
        '<a class="btn sm ghost" href="' + location.pathname + '?sync=1">同步模式再试</a>' +
        '<a class="btn sm ghost" href="' + location.pathname + '">刷新本页</a>' +
        '</p></div>');
      var btn = document.getElementById('retryBtn');
      if (btn) btn.onclick = function () { location.reload(); };
    }

    /* 一次性回退接口 */
    function fallbackOneShot(reason) {
      fetch('/api/chapter/' + bookId + '/' + idx + '?live=1')
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) { showError(d.error || reason, tries < MAX_TRIES); return; }
          body.innerHTML = d.html || '';
          Illus.reset(); Illus.scan(body);
          if (wc) wc.textContent = d.words || 0;
          addBadge();
          window.scrollTo(0, 0);
        })
        .catch(function () { showError('网络中断', tries < MAX_TRIES); });
    }

    function connect() {
      try {
        es = new EventSource('/api/chapter/' + bookId + '/' + idx + '/stream?r=' + Date.now());
      } catch (e) {
        fallbackOneShot('浏览器不支持流式');
        return;
      }

      es.onmessage = function (ev) {
        var d;
        try { d = JSON.parse(ev.data); } catch (e) { return; }
        append(d.html);
        words += d.words || 0;
        if (wc) wc.textContent = words;
      };

      es.addEventListener('done', function () {
        done = true;
        try { es.close(); } catch (e) {}
        addBadge();
        Illus.scan(body);
        Illus.paint();
      });

      es.addEventListener('error', function (ev) {
        if (done) return;
        try { es.close(); } catch (e) {}
        var msg = '';
        if (ev && ev.data) {
          try { msg = JSON.parse(ev.data).error || ''; } catch (e) {}
        }
        if (tries < MAX_TRIES) {
          tries += 1;
          /* 自动重试：内容已出就只补后面，没出就重连 */
          setTimeout(connect, 800 * tries);
          return;
        }
        if (!gotAny) fallbackOneShot(msg || '源站没响应');
        else showError('后面几页没取到：' + (msg || '连接中断'), false);
      });
    }

    connect();

    /* 4 秒没动静：第一次自动重试探路 */
    setTimeout(function () {
      if (!gotAny && !done && tries === 0) {
        tries += 1;
        try { es && es.close(); } catch (e) {}
        connect();
      }
    }, 4000);
  }

  /* ---------- 预加载：把后面几章在后台暖好，翻章就不用等 ---------- */
  var nextIdx = reader.dataset.next ? parseInt(reader.dataset.next, 10) : NaN;
  if (!isNaN(nextIdx)) {
    var warmUp = function () {
      fetch('/api/prefetch?bid=' + bookId + '&idx=' + idx + '&n=2')
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var btn = document.querySelector('.chapter-nav a[rel="next"]');
          if (btn && d && d.warm && d.warm.length) {
            btn.title = '下一章已缓存，点开即读';
            btn.classList.add('warm');
          }
        })
        .catch(function () {});
    };
    if ('requestIdleCallback' in window) {
      requestIdleCallback(warmUp, { timeout: 1500 });
    } else {
      setTimeout(warmUp, 400);
    }
  }

  /* ---------- 显示设置 ---------- */
  var KEY = 'nh-reader-settings';
  var def = { fontSize: 19, lineHeight: 190, measure: 46 };
  var st = def;
  try { st = Object.assign({}, def, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) {}

  var fSize = document.getElementById('fontSize');
  var fLine = document.getElementById('lineHeight');
  var fMeas = document.getElementById('measure');

  function paint() {
    root.style.setProperty('--fs', st.fontSize + 'px');
    root.style.setProperty('--lh', st.lineHeight + '%');
    root.style.setProperty('--measure', st.measure + 'em');
    if (fSize) fSize.value = st.fontSize;
    if (fLine) fLine.value = st.lineHeight;
    if (fMeas) fMeas.value = st.measure;
  }
  function save() {
    localStorage.setItem(KEY, JSON.stringify(st));
    paint();
  }
  paint();

  if (fSize) fSize.addEventListener('input', function () { st.fontSize = +this.value; save(); });
  if (fLine) fLine.addEventListener('input', function () { st.lineHeight = +this.value; save(); });
  if (fMeas) fMeas.addEventListener('input', function () { st.measure = +this.value; save(); });

  var settingsBtn = document.getElementById('settingsBtn');
  if (settingsBtn) settingsBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    settings.classList.toggle('open');
  });
  document.querySelectorAll('.swatch').forEach(function (b) {
    b.addEventListener('click', function () {
      root.setAttribute('data-theme', b.dataset.theme);
      localStorage.setItem('nh-theme', b.dataset.theme);
    });
  });
  document.addEventListener('click', function (e) {
    if (settings && !settings.contains(e.target) && e.target !== settingsBtn) settings.classList.remove('open');
  });

  /* ---------- 目录抽屉 ---------- */
  var tocBtn = document.getElementById('tocBtn');
  var tocClose = document.getElementById('tocClose');
  if (tocBtn) tocBtn.addEventListener('click', function () { drawer.classList.add('open'); });
  if (tocClose) tocClose.addEventListener('click', function () { drawer.classList.remove('open'); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      drawer && drawer.classList.remove('open');
      settings && settings.classList.remove('open');
    }
  });

  /* ---------- 键盘翻章 ---------- */
  document.addEventListener('keydown', function (e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    var prev = document.querySelector('.chapter-nav a[rel="prev"]');
    var next = document.querySelector('.chapter-nav a[rel="next"]');
    if (e.key === 'ArrowLeft' && prev) location.href = prev.href;
    if (e.key === 'ArrowRight' && next) location.href = next.href;
  });

  /* ---------- 滚动进度条 + 进度保存 ---------- */
  var bar = document.getElementById('scrollBar');
  function ratio() {
    var h = document.documentElement.scrollHeight - window.innerHeight;
    return h > 0 ? Math.min(1, window.scrollY / h) : 0;
  }
  function onScroll() {
    if (bar) bar.style.width = (ratio() * 100).toFixed(1) + '%';
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  function saveProgress() {
    try {
      var body = JSON.stringify({ book_id: +bookId, chapter_idx: idx, scroll: ratio() });
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/progress', new Blob([body], { type: 'application/json' }));
      } else {
        fetch('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body, keepalive: true });
      }
    } catch (e) {}
  }
  window.addEventListener('pagehide', saveProgress);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') saveProgress();
  });

  /* 从书架“继续阅读”回来时恢复滚动位置 */
  var posKey = 'nh-pos-' + bookId;
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(posKey) || 'null'); } catch (e) {}
  if (saved && saved.idx === idx && saved.scroll > 0.02) {
    window.scrollTo(0, (document.documentElement.scrollHeight - window.innerHeight) * saved.scroll);
  }
  window.addEventListener('pagehide', function () {
    try { localStorage.setItem(posKey, JSON.stringify({ idx: idx, scroll: ratio() })); } catch (e) {}
  });
})();
