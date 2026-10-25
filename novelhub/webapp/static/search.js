/* 聚合搜索：本地结果服务端直出，在线结果按"用户勾选的源"并发拉取 */
(function () {
  var cfg = window.NH_SEARCH;
  if (!cfg || !cfg.q) return;

  var status = document.getElementById('onlineStatus');
  var novelBlock = document.getElementById('novelBlock');
  var comicBlock = document.getElementById('comicBlock');
  var novelHits = document.getElementById('novelHits');
  var comicHits = document.getElementById('comicHits');
  var novelCount = document.getElementById('novelCount');
  var comicCount = document.getElementById('comicCount');
  var animeBlock = document.getElementById('animeBlock');
  var animeHits = document.getElementById('animeHits');
  var animeCount = document.getElementById('animeCount');

  var params = new URLSearchParams(location.search);

  /* 从 URL 恢复勾选状态（刷新/分享后保持一致） */
  var urlComics = params.get('comics');
  var urlNovels = params.get('novels');
  if (urlComics !== null) {
    var wanted = urlComics.split(',').filter(Boolean);
    document.querySelectorAll('input[data-group="comic"]').forEach(function (cb) {
      cb.checked = wanted.indexOf(cb.value) >= 0;
    });
  }
  var urlAnimes = params.get('animes');
  if (urlAnimes !== null) {
    var wantA = urlAnimes.split(',').filter(Boolean);
    document.querySelectorAll('input[data-group="anime"]').forEach(function (cb) {
      cb.checked = wantA.indexOf(cb.value) >= 0;
    });
  }
  if (urlNovels !== null) {
    var wantN = urlNovels.split(',').filter(Boolean);
    document.querySelectorAll('input[data-group="novel"]').forEach(function (cb) {
      cb.checked = wantN.indexOf(cb.value) >= 0;
    });
  } else if (params.get('novel') === '0') {
    document.querySelectorAll('input[data-group="novel"]').forEach(function (cb) {
      cb.checked = false;
    });
  }

  function selected(group) {
    return Array.prototype.slice
      .call(document.querySelectorAll('input[data-group="' + group + '"]:checked'))
      .map(function (cb) { return cb.value; });
  }

  function cover(url, title) {
    if (!url) return '<span class="cover-fallback">' + (title || '?').slice(0, 2) + '</span>';
    return '<img src="/api/img?u=' + encodeURIComponent(url) + '" loading="lazy" alt="">';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
    });
  }

  /* 小说卡片：已在书架 → 打开；索引里有 → 加入书架；只有书目 → 去索引找同名 */
  function novelCard(hit) {
    var href = hit.url;
    var badge = '';
    if (hit.action === 'add') {
      href = '/add?url=' + encodeURIComponent(hit.url || '');
      badge = '<span class="hit-act">加入书架</span>';
    } else if (hit.book_id) {
      /* 本地结果：url 是站内路径（/book/N 或 /read/N/M），直接用 */
      href = (hit.url && hit.url.charAt(0) === '/') ? hit.url : '/book/' + hit.book_id;
      badge = '<span class="hit-act ok">已在书架</span>';
    } else if (hit.action === 'meta') {
      /* Bangumi：只有书目。索引里有同名就给"去读"，没有就给"到索引里找" */
      if (hit.readable) {
        href = '/add?url=' + encodeURIComponent(hit.readable.url || '');
        badge = '<span class="hit-act">哔哩轻小说·去读</span>';
      } else {
        href = '/search?q=' + encodeURIComponent(hit.title || '') + '&kind=novel';
        badge = '<span class="hit-act ok">仅书目·查索引</span>';
      }
    }
    var sub = [hit.author, hit.category, hit.title_jp].filter(Boolean).join(' · ');
    return '<a class="hit" href="' + esc(href) + '"' +
      (hit.action === 'add' || (hit.action === 'meta' && hit.readable) ? ' target="_blank" rel="noreferrer"' : '') + '>' +
      '<span class="hit-cover">' + cover(hit.cover, hit.title) + '</span>' +
      '<span class="hit-body">' +
      '<span class="hit-title">' + esc(hit.title || '(无标题)') + '</span>' +
      '<span class="hit-sub">' + esc(sub) + '</span>' +
      (hit.intro ? '<span class="hit-tags">' + esc(hit.intro) + '</span>' : '') +
      '<span class="hit-src">' + esc(hit.source_name || hit.source) + badge + '</span>' +
      '</span></a>';
  }

  function animeCard(hit) {
    var sub = [hit.year, hit.remark].filter(Boolean).join(' · ');
    return '<a class="hit" href="' + esc(hit.url) + '">' +
      '<span class="hit-cover">' + cover(hit.cover, hit.title) + '</span>' +
      '<span class="hit-body">' +
      '<span class="hit-title">' + esc(hit.title || '(无标题)') + '</span>' +
      '<span class="hit-sub">' + esc(sub) + '</span>' +
      '<span class="hit-src">' + esc(hit.source_name || hit.source) + '</span>' +
      '</span></a>';
  }

  function comicCard(hit) {
    var href = '/comic/' + encodeURIComponent(hit.source) + '/' + encodeURIComponent(hit.source_id);
    var tags = (hit.tags || []).slice(0, 3).join(' · ');
    return '<a class="hit" href="' + href + '">' +
      '<span class="hit-cover">' + cover(hit.cover, hit.title) + '</span>' +
      '<span class="hit-body">' +
      '<span class="hit-title">' + esc(hit.title || '(无标题)') + '</span>' +
      '<span class="hit-sub">' + esc(hit.author || '') + '</span>' +
      (tags ? '<span class="hit-tags">' + esc(tags) + '</span>' : '') +
      '<span class="hit-src">' + esc(hit.source_name || hit.source) + '</span>' +
      '</span></a>';
  }

  function run() {
    var novels = selected('novel');
    var comics = selected('comic');
    var animes = selected('anime');
    if (novels.length === 0 && comics.length === 0 && animes.length === 0) {
      if (status) status.textContent = '没有勾选任何源。';
      return;
    }
    if (novelHits) { novelHits.innerHTML = ''; novelBlock.hidden = true; }
    if (comicHits) { comicHits.innerHTML = ''; comicBlock.hidden = true; }
    if (animeHits) { animeHits.innerHTML = ''; animeBlock.hidden = true; }

    var t0 = Date.now();
    var pending = 0;
    var notes = [];
    var tick = null;

    /* 进度计时，避免"看着像卡死" */
    function render() {
      var secs = ((Date.now() - t0) / 1000).toFixed(1);
      if (status) status.textContent = '用时 ' + secs + 's · ' + notes.join(' · ');
    }
    if (status) {
      notes.push('小说 ' + novels.length + ' / 漫画 ' + comics.length + ' / 番剧 ' + animes.length + ' 源');
      tick = setInterval(render, 200);
      render();
    }

    function settle(msg) {
      if (msg) notes.push(msg);
      pending -= 1;
      if (pending <= 0) {
        if (tick) clearInterval(tick);
        var secs = ((Date.now() - t0) / 1000).toFixed(1);
        if (status) status.textContent = '用时 ' + secs + 's · ' + notes.join(' · ');
      } else {
        render();
      }
    }

    /* 小说、漫画各发一个请求：谁先回来谁先出结果，互不拖累 */
    if (novels.length && novelHits) {
      pending += 1;
      fetch('/api/aggregate?q=' + encodeURIComponent(cfg.q) +
        '&kind=novel&novels=' + encodeURIComponent(novels.join(',')) + '&comics=')
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var rows = (d.novels || []);
          if (novelHits && rows.length) {
            novelBlock.hidden = false;
            novelHits.innerHTML = rows.map(novelCard).join('');
            var nsrc = {};
            rows.forEach(function (h) { nsrc[h.source_name || h.source] = 1; });
            novelCount.textContent = rows.length + ' 条 · ' + Object.keys(nsrc).join('/') + ' 命中';
          }
          var msg = '小说 ' + rows.length + ' 条';
          if (!rows.length) msg = '小说没命中';
          // 慢源不算失败：先给已经拿到的结果，说明还有谁在搜
          if (d.slow && d.slow.length) msg += '（' + d.slow.length + ' 个源较慢，已跳过）';
          if (d.errors && d.errors.length) msg += '（失败 ' + d.errors.length + '）';
          settle(msg);
        })
        .catch(function () { settle('小说请求失败'); });
    }

    /* 番剧按源拆请求：一个慢源（wujin 常 80s 超时）不再拖住其它源 */
    if (animes.length && animeHits) {
      var animeRows = [];
      var animeDone = 0;
      var animeErrs = 0;
      pending += 1;

      function paintAnime() {
        if (!animeRows.length) return;
        animeBlock.hidden = false;
        animeHits.innerHTML = animeRows.map(animeCard).join('');
        var asrc = {};
        animeRows.forEach(function (h) { asrc[h.source_name || h.source] = 1; });
        animeCount.textContent = animeRows.length + ' 条 · ' + Object.keys(asrc).join('/') + ' 命中';
      }

      function animeSettle() {
        animeDone += 1;
        if (animeDone < animes.length) {
          render();
          return;
        }
        var msg = '番剧 ' + animeRows.length + ' 条';
        if (!animeRows.length) msg = '番剧没命中';
        if (animeErrs) msg += '（失败 ' + animeErrs + '）';
        settle(msg);
      }

      animes.forEach(function (key) {
        fetch('/api/aggregate?q=' + encodeURIComponent(cfg.q) +
          '&kind=anime&animes=' + encodeURIComponent(key))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var rows = (d.animes || []);
            if (rows.length) {
              animeRows = animeRows.concat(rows);
              paintAnime();
            }
            if (d.errors && d.errors.length) animeErrs += 1;
            animeSettle();
          })
          .catch(function () { animeErrs += 1; animeSettle(); });
      });
    }
    if (comics.length && comicHits) {      pending += 1;
      fetch('/api/aggregate?q=' + encodeURIComponent(cfg.q) +
        '&kind=comic&comics=' + encodeURIComponent(comics.join(',')))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var rows = (d.comics || []);
          if (comicHits && rows.length) {
            comicBlock.hidden = false;
            comicHits.innerHTML = rows.map(comicCard).join('');
            var srcs = {};
            rows.forEach(function (h) { srcs[h.source] = 1; });
            comicCount.textContent = rows.length + ' 条 · ' +
              Object.keys(srcs).length + '/' + comics.length + ' 个源有结果';
          }
          var msg = '漫画 ' + rows.length + ' 条';
          if (!rows.length) msg = '漫画没命中';
          if (d.errors && d.errors.length) msg += '（失败 ' + d.errors.length + '）';
          settle(msg);
        })
        .catch(function () { settle('漫画请求失败'); });
    }
  }

  /* 全选 / 全不选 */
  function bindAll(onId, offId, group) {
    var on = document.getElementById(onId), off = document.getElementById(offId);
    if (on) on.onclick = function () {
      document.querySelectorAll('input[data-group="' + group + '"]')
        .forEach(function (cb) { cb.checked = true; });
      run();
    };
    if (off) off.onclick = function () {
      document.querySelectorAll('input[data-group="' + group + '"]')
        .forEach(function (cb) { cb.checked = false; });
      run();
    };
  }
  bindAll('allOn', 'allOff', 'comic');
  bindAll('novelAllOn', 'novelAllOff', 'novel');
  bindAll('animeAllOn', 'animeAllOff', 'anime');

  /* 用当前勾选重搜：写进 URL 并重新加载（保持可分享/可刷新） */
  var btn = document.getElementById('research');
  if (btn) btn.onclick = function () {
    var p = new URLSearchParams(location.search);
    p.delete('novel');
    p.set('novels', selected('novel').join(','));
    p.set('comics', selected('comic').join(','));
    p.set('animes', selected('anime').join(','));
    location.search = p.toString();
  };

  /* 建/续扫书目索引 */
  var buildBtn = document.getElementById('buildIndex');
  var idxStatus = document.getElementById('idxStatus');
  var polling = null;

  function poll() {
    fetch('/api/novelindex/status').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      if (d.error) {
        if (idxStatus) idxStatus.textContent = '索引出错：' + d.error;
        clearInterval(polling); polling = null;
        if (buildBtn) buildBtn.disabled = false;
        return;
      }
      if (idxStatus) {
        idxStatus.textContent = d.running
          ? '正在扫：' + d.pages + '/' + d.budget + ' 页 · 已索引 ' + d.count + ' 本 · ' + (d.list || '')
          : '索引完成：' + d.count + ' 本（扫了 ' + d.pages + ' 页）';
      }
      if (!d.running) {
        clearInterval(polling); polling = null;
        if (buildBtn) buildBtn.disabled = false;
        run();
      }
    }).catch(function () { });
  }

  /* 建/续扫 与 增量刷新 */
  var buildBtn = document.getElementById('buildIndex');
  var refreshBtn = document.getElementById('refreshIndex');
  var idxStatus = document.getElementById('idxStatus');
  var polling = null;

  function startIndex(mode, pages) {
    var body = new FormData();
    body.append('mode', mode);
    body.append('pages', String(pages));
    fetch('/api/novelindex/build', { method: 'POST', body: body })
      .then(function (r) { return r.json(); })
      .then(function () {
        if (polling) clearInterval(polling);
        polling = setInterval(poll, 1200);
        poll();
      })
      .catch(function () {
        if (idxStatus) idxStatus.textContent = '启动索引失败（网络中断）';
        if (buildBtn) buildBtn.disabled = false;
        if (refreshBtn) refreshBtn.disabled = false;
      });
  }

  if (buildBtn) buildBtn.onclick = function () {
    buildBtn.disabled = true;
    if (refreshBtn) refreshBtn.disabled = true;
    if (idxStatus) idxStatus.textContent = '开始扫描…';
    startIndex('full', 60);
  };

  if (refreshBtn) refreshBtn.onclick = function () {
    refreshBtn.disabled = true;
    if (buildBtn) buildBtn.disabled = true;
    if (idxStatus) idxStatus.textContent = '增量刷新：只扫新书/最近更新…';
    startIndex('refresh', 3);
  };

  run();
})();
