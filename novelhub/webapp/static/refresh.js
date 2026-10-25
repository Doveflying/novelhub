/* 加载失败 → 浮出「刷新」按钮。
 *
 * 覆盖这些情况：
 *   - 图片加载失败（封面、正文插图、漫画页）
 *   - 脚本里已有 .load-fail 失败块（正文流、漫画图片列表）
 *   - fetch 明显失败（聚合搜索、章节接口）
 *
 * 为什么不只靠 `error` 事件：`<img loading="lazy">` 的 error 什么时候派发
 * 取决于懒加载调度，实测在 Edge 里挂在 document 捕获阶段收不到（元素已经
 * 发请求并失败，但页面没收到事件）。所以这里用**主动检测**兜底：
 * 图片 `complete && naturalWidth === 0` 就是加载失败，扫一遍就能发现。
 */
(function () {
  var bar = null;
  var failCount = 0;
  var scanned = 0;

  function ensureBar() {
    if (bar) return bar;
    bar = document.createElement('div');
    bar.className = 'refresh-bar';
    bar.innerHTML =
      '<span class="rb-text"></span>' +
      '<button class="btn sm" type="button" data-rb="reload">刷新</button>' +
      '<button class="btn sm ghost" type="button" data-rb="close">×</button>';
    document.body.appendChild(bar);

    bar.addEventListener('click', function (e) {
      var act = e.target && e.target.getAttribute('data-rb');
      if (act === 'reload') location.reload();
      else if (act === 'close') hide();
    });
    return bar;
  }

  function hide() {
    if (bar) bar.classList.remove('on');
    failCount = 0;
    scanned = 0;
  }

  function show(msg, count) {
    ensureBar();
    var t = bar.querySelector('.rb-text');
    if (t) {
      t.textContent = count > 1 ? count + ' 处内容没加载出来' : (msg || '有内容没加载出来');
    }
    bar.classList.add('on');
    /* 故意不自动收起：失败是低频事件，让用户自己决定刷新还是关掉 */
  }

  /* 插图/漫画页自己有重试 UI，别再叠一层提示 */
  function selfHandled(img) {
    if (!img.closest) return false;
    if (img.closest('.illus-box')) return true;
    if (img.classList && img.classList.contains('comic-page')) return true;
    return false;
  }

  /* 扫一遍所有图片，返回失败数量 */
  function scanImages() {
    var imgs = document.querySelectorAll('img');
    var bad = 0;
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (selfHandled(img)) continue;
      if (img.dataset.rbOk === '1') continue;
      /* naturalWidth > 0 说明真加载出来了 */
      if (img.naturalWidth > 0) {
        img.dataset.rbOk = '1';
        continue;
      }
      /* complete 但宽度为 0 → 已失败 */
      if (img.complete) bad += 1;
    }
    return bad;
  }

  /* 兜底：有些浏览器/扩展把懒加载图换成占位元素，既不触发 error，
     也不会让 complete 变 true。对这类「看着是空的」图直接探一次真实状态。
     只探前 20 张，避免一次打太多请求。

     注意：必须用 Image() 探，不能用 fetch()。
     封面大多来自外站（bilinovel 等），<img> 能显示是因为图片请求不受 CORS 限制，
     但 fetch() 读不到 Access-Control-Allow-Origin 就一律报错 ——
     用 fetch 探会把「其实显示正常的外链封面」全判成失败，
     页面上就冒出「N 处内容没加载出来」的假警报。 */
  var probed = 0;
  function probeEmptyImages() {
    var imgs = document.querySelectorAll('img');
    var pending = 0;
    for (var i = 0; i < imgs.length && probed < 20; i++) {
      var img = imgs[i];
      if (selfHandled(img) || img.dataset.rbProbe === '1' || img.dataset.rbOk === '1') continue;
      if (img.naturalWidth > 0 || img.complete) continue;
      var src = img.getAttribute('src') || '';
      if (!src) continue;
      img.dataset.rbProbe = '1';
      probed += 1;
      pending += 1;
      (function (el, url) {
        var probe = new Image();
        var done = false;
        function ok() {
          if (done) return;
          done = true;
          el.dataset.rbOk = '1';
        }
        function bad() {
          if (done) return;
          done = true;
          el.dataset.rbBad = '1';
          failCount += 1;
          show(el.getAttribute('alt') || '图片', failCount);
        }
        probe.onload = ok;
        probe.onerror = bad;
        probe.referrerPolicy = 'no-referrer';
        probe.src = url;
        /* 8 秒还没结论就当它没问题，别把网络慢算成坏图 */
        setTimeout(function () {
          if (!done) {
            done = true;
            if (probe.naturalWidth > 0) el.dataset.rbOk = '1';
          }
        }, 8000);
      })(img, src);
    }
    return pending;
  }

  /* 已有的失败块 */
  function scanFailBlocks() {
    var blocks = document.querySelectorAll('.load-fail');
    if (!blocks.length) return 0;
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (b.dataset.rbAdded) continue;
      b.dataset.rbAdded = '1';
      var actions = b.querySelector('.fail-actions');
      if (actions && !actions.querySelector('[data-rb-inline]')) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn sm';
        btn.setAttribute('data-rb-inline', '1');
        btn.textContent = '刷新';
        btn.onclick = function () {
          location.reload();
        };
        actions.appendChild(btn);
      }
    }
    return blocks.length;
  }

  function check(why) {
    var badImgs = scanImages();
    var badBlocks = scanFailBlocks();
    var n = badImgs + badBlocks;
    if (n > 0) {
      if (n > failCount) failCount = n;
      show(why, failCount);
    }
  }

  /* 图片失败事件：能收到就立即响应（window 捕获 + 元素直挂双保险） */
  function onImgError(e) {
    var el = e.target;
    if (!el || el.tagName !== 'IMG' || selfHandled(el)) return;
    failCount += 1;
    show(el.getAttribute('alt') || '图片', failCount);
  }
  window.addEventListener('error', onImgError, true);

  /* 给每张图直接挂 onerror（新插入的图也能覆盖） */
  function wire(scope) {
    var imgs = (scope || document).querySelectorAll('img');
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (img.dataset.rbWired) continue;
      img.dataset.rbWired = '1';
      img.addEventListener('error', onImgError);
    }
  }

  function fullCheck(why) {
    wire();
    check(why);
    probeEmptyImages();
  }

  if (window.MutationObserver) {
    var mo = new MutationObserver(function () {
      fullCheck();
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  /* load 之后 + 几个时间点各扫一次：懒加载的图会陆续出结果 */
  window.addEventListener('load', function () {
    fullCheck('有内容没加载出来');
  });
  [600, 1800, 4000].forEach(function (ms) {
    setTimeout(function () {
      fullCheck('有内容没加载出来');
    }, ms);
  });

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    fullCheck();
  } else {
    document.addEventListener('DOMContentLoaded', function () {
      fullCheck();
    });
  }
})();
