/* 全局：主题切换 */
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
