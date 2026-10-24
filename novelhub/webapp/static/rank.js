/* 源排行榜：跑健康检查（全部 / 单行重测），轮询进度，完了刷新 */
(function () {
  var all = document.getElementById('runAll');
  var fast = document.getElementById('runFast');
  var prune = document.getElementById('pruneBad');
  var st = document.getElementById('rankStatus');
  var polling = null;

  function setBusy(b) {
    if (all) all.disabled = b;
    if (fast) fast.disabled = b;
    if (prune) prune.disabled = b;
    document.querySelectorAll('.retest').forEach(function (b2) { b2.disabled = b; });
  }

  function say(t) { if (st) st.textContent = t; }

  function poll() {
    fetch('/api/sourcehealth/status').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      say(d.running
        ? '正在测：' + d.done + '/' + d.total + ' 个源…'
        : (d.error ? '出错：' + d.error : '测完 ' + d.done + ' 个源，刷新中…'));
      if (!d.running) {
        clearInterval(polling);
        polling = null;
        setBusy(false);
        if (!d.error) location.reload();
      }
    }).catch(function () { });
  }

  function start(kinds, label) {
    setBusy(true);
    say(label + '…');
    var body = new FormData();
    body.append('kinds', kinds.join(','));
    fetch('/api/sourcehealth/run', { method: 'POST', body: body })
      .then(function (r) { return r.json(); })
      .then(function () {
        if (polling) clearInterval(polling);
        polling = setInterval(poll, 1200);
        poll();
      })
      .catch(function () {
        say('启动失败（网络中断）');
        setBusy(false);
      });
  }

  if (all) all.onclick = function () { start(['novel', 'comic', 'anime'], '开始测全部源'); };

  // 摘掉「在用但不可用」的源：改配置 + 禁用漫画源
  if (prune) prune.onclick = function () {
    var bad = [];
    document.querySelectorAll('.rank-table tbody tr[data-kind]').forEach(function (tr) {
      if (!tr.classList.contains('is-bad')) return;
      var use = tr.querySelector('.cell-use');
      if (use && use.textContent.indexOf('在用') >= 0) bad.push(tr.dataset.name || tr.dataset.key);
    });
    if (!bad.length) { say('没有「在用但不可用」的源，不用动'); return; }
    setBusy(true);
    say('正在摘掉：' + bad.join('、') + '…');
    fetch('/api/sourcehealth/prune', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          say(d.removed.length ? '已摘掉 ' + d.removed.join('、') + '，刷新中…' : '没有需要摘掉的源');
          if (d.removed.length) setTimeout(function () { location.reload(); }, 900);
          else setBusy(false);
        } else {
          say('摘除失败：' + (d.error || '未知错误'));
          setBusy(false);
        }
      })
      .catch(function () { say('摘除失败（网络中断）'); setBusy(false); });
  };

  // 只重测「当前显示为可用」的行：逐行打单源接口，坏源完全不碰
  if (fast) fast.onclick = function () {
    var rows = [];
    document.querySelectorAll('.rank-table tbody tr[data-kind]').forEach(function (tr) {
      if (!tr.classList.contains('is-bad')) rows.push(tr);
    });
    if (!rows.length) { say('没有可用的源可测'); return; }

    setBusy(true);
    var i = 0, okN = 0;
    function next() {
      if (i >= rows.length) {
        setBusy(false);
        say('重测完 ' + rows.length + ' 个可用源（' + okN + ' 个仍可用），刷新中…');
        setTimeout(function () { location.reload(); }, 600);
        return;
      }
      var tr = rows[i++];
      say('重测可用源：' + i + '/' + rows.length + '（' + (tr.dataset.name || tr.dataset.key) + '）…');
      var body = new FormData();
      body.append('kind', tr.dataset.kind);
      body.append('key', tr.dataset.key);
      body.append('name', tr.dataset.name || '');
      fetch('/api/sourcehealth/retest', { method: 'POST', body: body })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (d.ok && d.result) { paint(tr, d.result); if (d.result.ok) okN++; } })
        .catch(function () { })
        .then(next);
    }
    next();
  };

  // 单行重测：只打一个源，不刷新整页，原地更新那一行
  function paint(tr, r) {
    tr.classList.toggle('is-bad', !r.ok);
    tr.classList.toggle('is-warn', r.ok && r.count === 0);
    var q = function (s) { return tr.querySelector(s); };
    if (q('.cell-score')) q('.cell-score').textContent = r.score;
    if (q('.cell-ms')) q('.cell-ms').textContent = r.ms + 'ms';
    if (q('.cell-count')) q('.cell-count').textContent = r.count;
    if (q('.cell-err')) q('.cell-err').textContent = r.error || '';
    var stc = q('.cell-state');
    if (stc) {
      stc.innerHTML = !r.ok ? '<span class="pill bad">不可用</span>'
        : (r.count === 0 ? '<span class="pill warn">无结果</span>'
          : '<span class="pill ok">可用</span>');
    }
  }

  document.querySelectorAll('.retest').forEach(function (btn) {
    btn.onclick = function () {
      var tr = btn.closest('tr');
      var body = new FormData();
      body.append('kind', tr.dataset.kind);
      body.append('key', tr.dataset.key);
      body.append('name', tr.dataset.name || '');
      var old = btn.textContent;
      btn.disabled = true;
      btn.textContent = '测…';
      say('正在重测 ' + (tr.dataset.name || tr.dataset.key) + '…');
      fetch('/api/sourcehealth/retest', { method: 'POST', body: body })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok && d.result) {
            paint(tr, d.result);
            say('已重测 ' + (tr.dataset.name || tr.dataset.key) + '：'
              + d.result.score + ' 分 / ' + d.result.ms + 'ms / ' + d.result.count + ' 条');
          } else {
            say('重测失败：' + (d.error || '未知错误'));
          }
        })
        .catch(function () { say('重测失败（网络中断）'); })
        .then(function () {
          btn.disabled = false;
          btn.textContent = old;
        });
    };
  });
})();
