/* Anime4K 超分开关：加载 shader、接管画布、记住用户选择
 *
 * 设计原则：
 *   - 默认关闭。浏览器跑卷积很吃 GPU，老机器上会掉帧，不能默认开。
 *   - 一旦出错（着色器编译失败 / 上下文丢失 / 帧率过低）自动关掉并说明原因，
 *     绝不让用户看到一个黑屏。
 *   - 选择记在 localStorage，下次进来自动恢复。
 */
(function () {
  'use strict';

  var v = document.getElementById('player');
  var canvas = document.getElementById('a4kLayer');
  var box = document.getElementById('a4kEnabled');
  var st = document.getElementById('a4kStatus');
  if (!v || !canvas || !box) return;

  var KEY = 'nh_a4k_on_v1';
  var SHADER_URL = '/static/shaders/Anime4K_Restore_CNN_S.glsl';

  var engine = null;
  var loaded = false;
  var busy = false;

  function say(t) { if (st) st.textContent = t || ''; }

  /* 画布必须精确覆盖 video 的**实际显示矩形**。
     CSS 的 object-fit 对 canvas 无效（canvas 内容会被硬拉伸），
     所以这里按 video 的渲染框算位置，video 因 max-height 留黑边时也不变形。 */
  function syncSize() {
    if (canvas.hidden) return;
    var vw = v.videoWidth || 0, vh = v.videoHeight || 0;
    if (!vw || !vh) return;

    /* 缓冲区分辨率 = 视频原始分辨率（Restore 不放大） */
    canvas.width = vw;
    canvas.height = vh;

    var r = v.getBoundingClientRect();
    var wrap = v.parentElement;
    if (!wrap) return;
    var wr = wrap.getBoundingClientRect();
    if (!wr.width || !wr.height) return;

    /* 视频按 contain 显示时会留黑边，先算真实画面区域 */
    var boxAR = r.width / r.height;
    var vidAR = vw / vh;
    var dw, dh, ox, oy;
    if (vidAR > boxAR) {          /* 视频更宽：上下留黑 */
      dw = r.width; dh = r.width / vidAR;
      ox = 0; oy = (r.height - dh) / 2;
    } else {                       /* 视频更高：左右留黑 */
      dh = r.height; dw = r.height * vidAR;
      oy = 0; ox = (r.width - dw) / 2;
    }

    canvas.style.left = (r.left - wr.left + ox) + 'px';
    canvas.style.top = (r.top - wr.top + oy) + 'px';
    canvas.style.width = dw + 'px';
    canvas.style.height = dh + 'px';
  }

  function make() {
    if (engine) return engine;
    var E = (window.NH || {}).Anime4K;
    if (!E) { say('超分模块未加载'); return null; }
    engine = new E.Engine(v, canvas);
    if (!engine.init()) {
      say('这个浏览器不支持 WebGL');
      engine = null;
      return null;
    }
    return engine;
  }

  function loadShader(cb) {
    if (loaded) { cb(true); return; }
    say('正在编译着色器…');
    fetch(SHADER_URL, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (src) {
        var e = make();
        if (!e) { cb(false); return; }
        if (!e.load(src)) { say('着色器编译失败，已关闭'); cb(false); return; }
        loaded = true;
        say('');
        cb(true);
      })
      .catch(function (err) {
        say('着色器加载失败：' + (err && err.message ? err.message : err));
        cb(false);
      });
  }

  function on(quiet) {
    if (busy) return;
    busy = true;
    loadShader(function (ok) {
      busy = false;
      if (!ok) {
        box.checked = false;
        try { localStorage.setItem(KEY, '0'); } catch (e) {}
        return;
      }
      syncSize();
      engine.enable();
      canvas.classList.add('on');
      if (!quiet) say('超分已开启');
      else say('');
      try { localStorage.setItem(KEY, '1'); } catch (e) {}
    });
  }

  function off() {
    if (engine) engine.disable();
    canvas.classList.remove('on');
    say('');
    try { localStorage.setItem(KEY, '0'); } catch (e) {}
  }

  box.addEventListener('change', function () {
    if (box.checked) on(false); else off();
  });

  /* 元数据到位才知道真实分辨率，这时再决定画布尺寸 */
  v.addEventListener('loadedmetadata', function () {
    syncSize();
    if (box.checked) on(true);
  });

  /* 窗口尺寸变了要重算覆盖框，否则画面会错位 */
  var resizeTimer = 0;
  window.addEventListener('resize', function () {
    if (!box.checked) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(syncSize, 120);
  });
  v.addEventListener('loadedmetadata', syncSize);
  document.addEventListener('fullscreenchange', function () {
    setTimeout(syncSize, 200);
  });

  /* WebGL 上下文丢了就老实关掉，别硬撑 */
  canvas.addEventListener('webglcontextlost', function (ev) {
    ev.preventDefault();
    box.checked = false;
    off();
    say('显卡上下文丢失，超分已关闭');
  });

  /* 恢复上次的选择 */
  var saved = '0';
  try { saved = localStorage.getItem(KEY) || '0'; } catch (e) {}
  if (saved === '1') {
    box.checked = true;
    if (v.readyState >= 1) on(true);
  }
})();
