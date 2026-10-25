/* 漫画阅读：先取图片地址，再逐张懒加载；失败自愈 */
(function () {
  var viewer = document.getElementById('viewer');
  if (!viewer) return;

  var key = viewer.dataset.key;
  var comicId = viewer.dataset.comic;
  var epId = viewer.dataset.ep;
  var pages = document.getElementById('comicPages');
  var loading = document.getElementById('comicLoading');
  var titleEl = document.getElementById('comicTitle');
  var epEl = document.getElementById('epTitle');
  var tries = 0;
  var MAX_TRIES = 2;

  var api = '/api/comic/ep?key=' + encodeURIComponent(key) +
    '&comic=' + encodeURIComponent(comicId) + '&ep=' + encodeURIComponent(epId);

  function fail(msg, canRetry) {
    if (!loading) return;
    loading.innerHTML = '<div class="load-fail">' +
      '<p class="sk-error">取这一话失败：' + (msg || '未知错误') + '</p>' +
      (canRetry ? '' : '<p class="hint">可能这个源没有这一话、需要登录，或被下架。可以换个源试试。</p>') +
      '<p class="fail-actions">' +
      (canRetry ? '<button class="btn sm" id="retryEp">重新加载</button>' : '') +
      '<a class="btn sm ghost" href="' + location.pathname + '">刷新本页</a>' +
      '<a class="btn sm ghost" href="/comic/' + encodeURIComponent(key) + '/' + encodeURIComponent(comicId) + '">回章节</a>' +
      '</p></div>';
    var b = document.getElementById('retryEp');
    if (b) b.onclick = function () { location.reload(); };
  }

  function render(urls) {
    if (loading) loading.remove();
    var frag = document.createDocumentFragment();
    urls.forEach(function (u, i) {
      var img = document.createElement('img');
      img.loading = i === 0 ? 'eager' : 'lazy';   /* 第一张立刻加载，其余滚到再拉 */
      img.decoding = 'async';
      img.alt = '';
      img.className = 'comic-page';
      img.dataset.index = i + 1;
      img.dataset.src = u;
      img.src = '/api/img?u=' + encodeURIComponent(u);
      img.onerror = function () {
        /* 单张失败：标红，点一下重试（加时间戳绕过缓存） */
        img.classList.add('broken');
        img.alt = '第 ' + (i + 1) + ' 张加载失败，点击重试';
        img.onclick = function () {
          img.classList.remove('broken');
          img.alt = '';
          img.onclick = null;
          img.src = '/api/img?u=' + encodeURIComponent(img.dataset.src) + '&r=' + Date.now();
        };
      };
      frag.appendChild(img);
    });
    pages.appendChild(frag);
  }

  function load() {
    fetch(api + '&r=' + Date.now()).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) {
        if (tries < MAX_TRIES) { tries += 1; setTimeout(load, 700 * tries); return; }
        fail(d.error, tries < MAX_TRIES);
        return;
      }
      var urls = d.urls || [];
      if (!urls.length) {
        if (tries < MAX_TRIES) { tries += 1; setTimeout(load, 700 * tries); return; }
        fail('这一话没有图片', false);
        return;
      }
      render(urls);
    }).catch(function () {
      if (tries < MAX_TRIES) { tries += 1; setTimeout(load, 700 * tries); return; }
      fail('网络中断', false);
    });
  }

  /* 章节导航：拉一次详情，按顺序给出上一话/下一话 */
  function wirePager() {
    var infoApi = '/api/comic/info?key=' + encodeURIComponent(key) + '&comic=' + encodeURIComponent(comicId);
    fetch(infoApi).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      var info = d.info || {};
      if (titleEl && info.title) titleEl.textContent = info.title;
      var groups = info.chapters || {};
      var flat = [];
      Object.keys(groups).forEach(function (g) {
        var eps = groups[g];
        if (eps && typeof eps === 'object') {
          Object.keys(eps).forEach(function (id) { flat.push({ id: id, title: eps[id] }); });
        }
      });
      var at = flat.findIndex(function (c) { return String(c.id) === String(epId); });
      if (at < 0) return;
      if (epEl) epEl.textContent = flat[at].title || ('第 ' + epId + ' 话');
      /* 把当前话标题写回进度，书架就能显示「读到 第1话」 */
      if (flat[at].title) {
        fetch('/api/comic/progress?key=' + encodeURIComponent(key) +
          '&comic=' + encodeURIComponent(comicId) +
          '&ep=' + encodeURIComponent(epId) +
          '&t=' + encodeURIComponent(flat[at].title)).catch(function () {});
      }
      // 顶部工具条和底部出口各一对，href 保持一致
      var prev = document.getElementById('prevEp');
      var next = document.getElementById('nextEp');
      var prev2 = document.getElementById('prevEp2');
      var next2 = document.getElementById('nextEp2');
      if (at > 0) {
        var prevUrl = '/comic/' + encodeURIComponent(key) + '/' + encodeURIComponent(comicId) + '/' +
          flat[at - 1].id + '?t=' + encodeURIComponent(flat[at - 1].title || '');
        if (prev) { prev.href = prevUrl; prev.hidden = false; }
        if (prev2) { prev2.href = prevUrl; prev2.hidden = false; }
      }
      if (at + 1 < flat.length) {
        var nextUrl = '/comic/' + encodeURIComponent(key) + '/' + encodeURIComponent(comicId) + '/' +
          flat[at + 1].id + '?t=' + encodeURIComponent(flat[at + 1].title || '');
        if (next) { next.href = nextUrl; next.hidden = false; }
        if (next2) { next2.href = nextUrl; next2.hidden = false; }
      }
    }).catch(function () {});
  }

  load();
  wirePager();
})();

/* 返回键：有历史就退历史（从章节页/搜索进来的都自然回去），
   没历史就按 href 回章节页 —— href 留着兜底。 */
(function () {
  var back = document.getElementById('comicBack');
  if (!back) return;
  back.addEventListener('click', function (e) {
    if (window.history.length > 1) {
      e.preventDefault();
      window.history.back();
    }
  });
})();
