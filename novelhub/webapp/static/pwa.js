/* 书萤 · PWA 装配
 *
 * 三件事，都很克制：
 *   1. 注册 Service Worker（只在 https 或 localhost 下有意义）
 *   2. 安卓/桌面 Chrome 弹「装到主屏」时，自己接管这个提示
 *   3. 补几个移动端手感：双击/双指缩放别乱触发、iOS 100vh 修正
 *
 * 不做的：不弹「请安装我们的 App」横幅，不拦截后退键。
 */
(function () {
  'use strict';

  var isStandalone =
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true;

  if (isStandalone) {
    document.documentElement.classList.add('standalone');
  }

  /* ---------- iOS：把 100vh 换成真实可视高度 ---------- */
  function setVH() {
    var vh = window.innerHeight * 0.01;
    document.documentElement.style.setProperty('--vh', vh + 'px');
  }
  setVH();
  window.addEventListener('resize', setVH, { passive: true });
  window.addEventListener('orientationchange', function () {
    setTimeout(setVH, 120);
  }, { passive: true });

  /* ---------- Service Worker ---------- */
  if ('serviceWorker' in navigator) {
    // file:// 和局域网 http 都不注册：SW 需要安全上下文，硬注册只会报错刷控制台
    var secure = location.protocol === 'https:' ||
      location.hostname === 'localhost' ||
      location.hostname === '127.0.0.1';
    if (secure) {
      window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
          .catch(function () { /* 注册失败不影响阅读 */ });
      });
    }
  }

  /* ---------- 安装提示 ---------- */
  var deferred = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferred = e;
    // 书架页右上角有个安静的「装到主屏」按钮，插进去
    if (document.body.dataset.page !== 'index') return;
    var host = document.querySelector('.appbar-actions') || document.querySelector('.appbar');
    if (!host || document.getElementById('installBtn')) return;
    var btn = document.createElement('button');
    btn.id = 'installBtn';
    btn.className = 'icon-btn';
    btn.type = 'button';
    btn.title = '装到主屏';
    btn.setAttribute('aria-label', '装到主屏');
    btn.innerHTML = '<svg class="ico"><use href="#i-download"/></svg>';
    btn.addEventListener('click', function () {
      if (!deferred) return;
      deferred.prompt();
      deferred.userChoice.then(function () { deferred = null; btn.remove(); });
    });
    host.appendChild(btn);
  });

  window.addEventListener('appinstalled', function () {
    deferred = null;
    var b = document.getElementById('installBtn');
    if (b) b.remove();
  });

  /* ---------- 移动端手感 ---------- */
  // 双击缩放会让阅读页误触（想翻页结果放大了），限制成单击
  var lastTouch = 0;
  document.addEventListener('touchend', function (e) {
    var now = Date.now();
    if (now - lastTouch <= 300) e.preventDefault();
    lastTouch = now;
  }, { passive: false });

  // 阅读页左右滑动翻章：只在正文区生效，不抢侧栏/抽屉的横向滚动
  var body = document.body;
  var startX = 0, startY = 0, tracking = false;
  document.addEventListener('touchstart', function (e) {
    if (!body.classList.contains('reading')) return;
    if (e.touches.length !== 1) return;
    var t = e.target;
    if (t.closest('.drawer, .settings-pop, .reader-dock, a, button, input')) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    tracking = true;
  }, { passive: true });

  document.addEventListener('touchend', function (e) {
    if (!tracking) return;
    tracking = false;
    var t = e.changedTouches[0];
    var dx = t.clientX - startX;
    var dy = t.clientY - startY;
    // 横向位移要明显大于纵向，否则当滚动处理
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.6) return;
    var prev = document.querySelector('.chapter-nav a[rel="prev"]');
    var next = document.querySelector('.chapter-nav a[rel="next"]');
    if (dx > 0 && prev) location.href = prev.href;
    if (dx < 0 && next) location.href = next.href;
  }, { passive: true });
})();
