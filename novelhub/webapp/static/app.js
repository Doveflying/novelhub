/* 全局：主题切换 + 刷新 */
(function () {
  var root = document.documentElement;
  var btn = document.getElementById('themeBtn');
  var order = ['paper', 'sepia', 'green', 'night'];
  function apply(t) {
    root.setAttribute('data-theme', t);
    localStorage.setItem('nh-theme', t);
  }
  if (btn) {
    btn.addEventListener('click', function () {
      var cur = root.getAttribute('data-theme') || 'paper';
      apply(order[(order.indexOf(cur) + 1) % order.length]);
    });
  }
})();

/* 刷新键：加载不出来时点一下。
 *
 * 为什么做成常驻而不是只在失败时出现：图片走 /api/img 代理，失败是站点/网络
 * 侧的偶发问题，浏览器对懒加载图不一定会派发 error（Edge 会替换成占位元素），
 * 靠事件判断不可靠。常驻一个按钮最稳，失败浮条（refresh.js）只是额外提示。 */
(function () {
  var btn = document.getElementById('refreshBtn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    btn.classList.add('spinning');
    /* 硬刷新：绕过浏览器缓存，把失败的图/接口都重新拉一遍 */
    location.reload();
  });
})();
