/* 番剧播放器：m3u8 走本地 /api/hls 代理 + hls.js，进度回写 */
(function () {
  var v = document.getElementById('player');
  if (!v) return;
  var st = document.getElementById('playerStatus');
  var src = v.dataset.src || '';
  var saved = parseFloat(v.dataset.pos || '0') || 0;
  var lastSave = 0;

  function say(t) { if (st) st.textContent = t; }

  /* 源站地址 → 本地代理 */
  function proxied(u) {
    return '/api/hls?u=' + encodeURIComponent(u);
  }

  var hls = null;
  var isM3u8 = /\.m3u8(\?|$)/i.test(src);

  function startNative() {
    v.src = src;
    say('直链播放（mp4/webm）');
  }

  function onReady() {
    if (saved > 5) {
      try { v.currentTime = saved; } catch (e) {}
      say('已跳到上次位置 ' + Math.floor(saved) + 's');
    } else {
      say('');
    }
  }

  if (!src) {
    say('这一集没有可播地址');
  } else if (isM3u8 && window.Hls && window.Hls.isSupported()) {
    hls = new window.Hls({ maxBufferLength: 30, enableWorker: true });
    hls.on(window.Hls.Events.MANIFEST_PARSED, function () {
      say('开始播放 · HLS');
      onReady();
      v.play().catch(function () { say('点一下播放按钮开始（浏览器拦截了自动播放）'); });
    });
    hls.on(window.Hls.Events.ERROR, function (_e, data) {
      if (!data || !data.fatal) return;
      if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
        say('网络错误，正在重试…');
        hls.startLoad();
      } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
        say('解码错误，正在恢复…');
        hls.recoverMediaError();
      } else {
        say('播放失败：' + (data.details || '未知'));
        hls.destroy();
      }
    });
    hls.loadSource(proxied(src));
    hls.attachMedia(v);
  } else if (isM3u8 && v.canPlayType('application/vnd.apple.mpegurl')) {
    /* Safari 原生支持，但也走代理，避免 Referer 被拦 */
    v.src = proxied(src);
    v.addEventListener('loadedmetadata', onReady);
    say('原生 HLS 播放');
  } else if (isM3u8) {
    say('这个浏览器不支持 HLS，请换 Chrome/Edge 或改用直链源');
  } else {
    startNative();
    v.addEventListener('loadedmetadata', onReady);
  }

  /* 进度回写：每 10 秒或暂停时 */
  function save(force) {
    var now = Date.now();
    if (!force && now - lastSave < 10000) return;
    lastSave = now;
    if (!v.duration || !isFinite(v.duration)) return;
    var body = JSON.stringify({
      source: v.dataset.source,
      source_id: v.dataset.sid,
      ep_idx: +v.dataset.epi,
      ep_title: v.dataset.eptitle,
      ep_url: src,
      road: v.dataset.road,
      total: +v.dataset.total,
      position: v.currentTime
    });
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/anime/progress', new Blob([body], { type: 'application/json' }));
      } else {
        fetch('/api/anime/progress', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body, keepalive: true
        });
      }
    } catch (e) {}
  }
  v.addEventListener('timeupdate', function () { save(false); });
  v.addEventListener('pause', function () { save(true); });
  window.addEventListener('pagehide', function () { save(true); });

  /* 播完自动下一集 */
  v.addEventListener('ended', function () {
    save(true);
    var next = document.querySelector('.player-head a.btn:not(.ghost)');
    if (next) next.click();
  });
})();
