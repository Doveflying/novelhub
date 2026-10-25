/* 弹幕 UI：把 danmaku.js 的渲染器接到 /api/danmu 上，并绑定设置面板。 */
(function () {
  'use strict';

  var v = document.getElementById('player');
  var cv = document.getElementById('danmuLayer');
  if (!v || !cv || !window.NovelDanmaku) return;

  var statusEl = document.getElementById('dmStatus');
  var panel = document.getElementById('dmPanel');
  var enableEl = document.getElementById('dmEnabled');

  function say(t) { if (statusEl) statusEl.textContent = t; }

  var dm = new window.NovelDanmaku.Danmaku(v, cv, {
    onStatus: function (t) { say(t); }
  });

  /* ---------------- 设置面板 ---------------- */
  var P = dm.prefs;

  function bindRange(id, key, valId, fmt) {
    var el = document.getElementById(id);
    var vl = document.getElementById(valId);
    if (!el) return;
    el.value = P[key];
    if (vl) vl.textContent = fmt ? fmt(P[key]) : P[key];
    el.addEventListener('input', function () {
      var x = parseFloat(el.value);
      dm.setPrefs({ [key]: x });
      if (vl) vl.textContent = fmt ? fmt(x) : x;
    });
  }

  function bindCheck(id, key) {
    var el = document.getElementById(id);
    if (!el) return;
    el.checked = !!P[key];
    el.addEventListener('change', function () {
      dm.setPrefs({ [key]: el.checked });
    });
  }

  bindRange('dmOpacity', 'opacity', 'dmOpacityVal', function (x) { return Math.round(x * 100) + '%'; });
  bindRange('dmFontSize', 'fontSize', 'dmFontSizeVal');
  bindRange('dmSpeed', 'speed', 'dmSpeedVal', function (x) { return x.toFixed(1) + 'x'; });
  bindRange('dmArea', 'area', 'dmAreaVal', function (x) { return Math.round(x * 100) + '%'; });

  bindCheck('dmScroll', 'showScroll');
  bindCheck('dmTop', 'showTop');
  bindCheck('dmBottom', 'showBottom');
  bindCheck('dmBold', 'bold');
  bindCheck('dmOutline', 'outline');
  bindCheck('dmMerge', 'mergeSame');

  var bw = document.getElementById('dmBlockWords');
  if (bw) {
    bw.value = P.blockWords || '';
    bw.addEventListener('input', function () {
      dm.setPrefs({ blockWords: bw.value });
    });
  }

  var setBtn = document.getElementById('dmSettings');
  if (setBtn) {
    setBtn.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
    });
  }

  if (enableEl) {
    enableEl.checked = !!P.enabled;
    enableEl.addEventListener('change', function () {
      dm.setPrefs({ enabled: enableEl.checked });
      if (!enableEl.checked) say('弹幕已关闭');
      else say(dm.count ? ('弹幕 ' + dm.count + ' 条') : '无弹幕');
    });
  }

  /* ---------------- 拉弹幕 ---------------- */
  var animeTitle = v.dataset.anime || '';
  /* 优先用分集名里抠出来的真实集号；抠不到才退回列表下标 +1。
     下标当集号在分集乱序、含总集篇/OVA 时会把弹幕串到别的集上。 */
  var epNo = parseInt(v.dataset.epno, 10) ||
             (parseInt(v.dataset.epi, 10) || 0) + 1;

  function loadDanmu() {
    if (!animeTitle) { say('无番名，跳过弹幕'); return; }
    say('正在匹配弹幕…');
    var url = '/api/danmu?anime=' + encodeURIComponent(animeTitle) +
              '&ep=' + encodeURIComponent(epNo);
    /* 把集名也带上：弹弹play 的分集里混着总集篇/OVA，
       光靠集号会错位，服务端会用集名做二次对齐 */
    var epTitle = v.dataset.eptitle || '';
    if (epTitle) url += '&eptitle=' + encodeURIComponent(epTitle);
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) {
          if (j.configured === false) say('未配置弹弹play凭据');
          else say('弹幕加载失败：' + (j.error || '未知'));
          return;
        }
        dm.load(j.comments || []);
        if (j.episode_title) say('弹幕 ' + j.count + ' 条 · ' + j.episode_title);
      })
      .catch(function (e) { say('弹幕请求异常'); });
  }

  var reloadBtn = document.getElementById('dmReload');
  if (reloadBtn) reloadBtn.addEventListener('click', loadDanmu);

  loadDanmu();
})();
