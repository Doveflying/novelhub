/* 书萤 · 聊天页
   和 QQ / 微信一样的心智：消息发出去就留在那儿，刷新还在；断了能重发。
   后端 /api/chat 是 SSE，所以用 fetch + ReadableStream 手撸，不走 EventSource（它只能 GET）。 */
(function () {
  "use strict";

  var root = document.getElementById("im");
  if (!root) return;

  var thread = document.getElementById("imThread");
  var intro = document.getElementById("imIntro");
  var form = document.getElementById("imForm");
  var input = document.getElementById("imInput");
  var sendBtn = document.getElementById("imSend");
  var clearBtn = document.getElementById("imClear");
  var stateEl = document.getElementById("imState");

  var NAME = root.dataset.name || "她";
  var WHO = root.dataset.who || "miyagi";
  var READY = root.dataset.ready === "1";
  var KEY = "nh-chat-v2:" + WHO;
  var SID_KEY = "nh-chat-sid:" + WHO;

  var TOOL_LABEL = {
    shelf_overview: "正在数书架",
    search_shelf: "正在翻书架",
    reading_status: "正在看进度",
    comic_status: "正在看漫画进度",
    anime_status: "正在翻番剧单",
    source_health: "正在体检书源",
    history_digest: "正在查档案",
    search_online: "正在上网找",
    add_to_shelf: "正在往架上放"
  };

  /* ---------- 状态 ---------- */
  var sid = "";
  try {
    sid = localStorage.getItem(SID_KEY) || "";
    if (!sid) {
      sid = "web-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
      localStorage.setItem(SID_KEY, sid);
    }
  } catch (e) { sid = "web"; }

  var msgs = [];          // {r:'me'|'her'|'sys', t:'', ts:number, kind?:'err'|'tool'|'note'}
  var busy = false;
  var ctrl = null;
  var lastSent = "";
  var lastToolRow = null;

  function save() {
    try {
      localStorage.setItem(KEY, JSON.stringify(msgs.slice(-200)));
    } catch (e) {}
  }

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) return [];
      var v = JSON.parse(raw);
      return Array.isArray(v) ? v.filter(function (m) { return m && m.r && typeof m.t === "string"; }) : [];
    } catch (e) { return []; }
  }

  /* ---------- 渲染 ---------- */
  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function timeLabel(ts) {
    var d = new Date(ts);
    var now = new Date();
    var sameDay = d.toDateString() === now.toDateString();
    var yes = new Date(now.getTime() - 86400000).toDateString() === d.toDateString();
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (sameDay) return "今天 " + hm;
    if (yes) return "昨天 " + hm;
    return (d.getMonth() + 1) + "月" + d.getDate() + "日 " + hm;
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  function linkify(html) {
    return html.replace(/(https?:\/\/[^\s<）)】]+)/g, function (u) {
      return '<a href="' + u + '" target="_blank" rel="noreferrer noopener">' + u + "</a>";
    });
  }

  function bodyHtml(text) {
    return linkify(escapeHtml(text));
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function avatar(role) {
    if (role === "me") {
      var a = el("span", "im-ava me", "我");
      a.setAttribute("aria-hidden", "true");
      return a;
    }
    /* 她的头像就是顶栏那张图：从模板里摸出来，省得再传一遍 URL */
    var b = el("span", "im-ava");
    var pic = document.querySelector(".im-head .im-ava img");
    if (pic) {
      var im = document.createElement("img");
      im.src = pic.getAttribute("src");
      im.alt = "";
      im.width = 34;
      im.height = 34;
      b.appendChild(im);
    } else {
      b.textContent = (NAME || "她").slice(0, 1);
    }
    b.setAttribute("aria-hidden", "true");
    return b;
  }

  function nearBottom() {
    return thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
  }

  function toBottom(force) {
    if (force || nearBottom()) thread.scrollTop = thread.scrollHeight;
  }

  function addTime(ts) {
    thread.appendChild(el("div", "im-time", timeLabel(ts)));
  }

  /* 一条真实消息；返回 bubble 节点，流式时往里追加 */
  function addRow(role, text, ts, fail) {
    var row = el("div", "im-row " + (role === "me" ? "me" : "her") + (fail ? " fail" : ""));
    row.appendChild(avatar(role));
    var bubble = el("div", "im-bubble");
    bubble.innerHTML = bodyHtml(text || "");
    row.appendChild(bubble);
    thread.appendChild(row);
    return bubble;
  }

  function addSys(text, kind) {
    var row = el("div", "im-sys" + (kind ? " " + kind : ""));
    row.appendChild(el("span", null, text));
    thread.appendChild(row);
    return row;
  }

  function addErr(text, resendText) {
    var row = addSys(text, "err");
    if (resendText) {
      var btn = el("button", null, "重试");
      btn.type = "button";
      btn.addEventListener("click", function () {
        row.remove();
        send(resendText, true);
      });
      row.appendChild(btn);
    }
    return row;
  }

  /* 整段重画：用于首次加载历史 */
  function renderAll() {
    thread.innerHTML = "";
    if (!msgs.length) {
      if (intro) thread.appendChild(intro);
      return;
    }
    var prevTs = 0, prevRole = "", lastMe = "";
    msgs.forEach(function (m) {
      if (m.ts && (!prevTs || m.ts - prevTs > 5 * 60 * 1000)) addTime(m.ts);
      if (m.r === "sys") {
        if (m.kind === "err") {
          addErr(m.t, lastMe);
        } else {
          var row = el("div", "im-sys" + (m.kind ? " " + m.kind : ""));
          row.appendChild(el("span", null, m.t));
          thread.appendChild(row);
        }
        prevRole = "";
      } else {
        if (m.r === "me") lastMe = m.t;
        var bubble = addRow(m.r, m.t, m.ts, m.kind === "fail");
        var currentRow = bubble.parentNode;
        if (prevRole === m.r) currentRow.classList.add("same");
        else currentRow.classList.add("head");
        prevRole = m.r;
      }
      prevTs = m.ts || prevTs;
    });
    toBottom(true);
  }

  /* ---------- 输入框 ---------- */
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, Math.round(window.innerHeight * 0.34)) + "px";
  }

  function refreshSend() {
    // 生成中：按钮不禁用，改成「停止」，点一下中断当前回复
    sendBtn.dataset.mode = busy ? "stop" : "send";
    sendBtn.setAttribute("aria-label", busy ? "停止生成" : "发送");
    sendBtn.disabled = busy ? false : (!input.value.trim() || !READY);
  }

  function setBusy(on, label) {
    busy = on;
    root.dataset.busy = on ? "1" : "0";
    if (stateEl) stateEl.textContent = on ? (label || "正在输入…") : "在线";
    refreshSend();
  }

  /* ---------- SSE ---------- */
  function parseChunk(buf, onEvent) {
    var idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      var block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      var name = "message", data = "";
      block.split("\n").forEach(function (line) {
        if (line.slice(0, 6) === "event:") name = line.slice(6).trim();
        else if (line.slice(0, 5) === "data:") data += line.slice(5).trim();
        else if (line.charAt(0) === ":") { /* 心跳 */ }
      });
      if (data) {
        var payload = null;
        try { payload = JSON.parse(data); } catch (e) { payload = null; }
        if (payload) onEvent(name, payload);
      }
    }
    return buf;
  }

  function errText(err) {
    var m = (err && err.message) || "";
    if (/Failed to fetch|NetworkError|Load failed|Network request/i.test(m)) {
      return "连不上本地服务，先确认 nh.py serve 还在跑";
    }
    if (/HTTP 5/.test(m)) return "服务那边出错了（" + m + "）";
    if (/HTTP 4/.test(m)) return "请求被拒了（" + m + "）";
    return m || "未知错误";
  }

  function send(text, resend) {
    text = (text == null ? input.value : text).trim();
    if (!text || busy || !READY) return;
    lastSent = text;

    if (intro && intro.parentNode) intro.remove();
    if (!msgs.length || Date.now() - (msgs[msgs.length - 1].ts || 0) > 5 * 60 * 1000) addTime(Date.now());

    if (!resend) {
      var prev = msgs.length ? msgs[msgs.length - 1] : null;
      var headRow = addRow("me", text, Date.now(), false);
      if (!prev || prev.r !== "me") headRow.parentNode.classList.add("head");
      else headRow.parentNode.classList.add("same");
      msgs.push({ r: "me", t: text, ts: Date.now() });
      save();
    }

    input.value = "";
    autosize();
    refreshSend();
    toBottom(true);

    /* 等字：先放三点，来正文后立刻换成内容 */
    var pending = el("div", "im-row her head");
    pending.appendChild(avatar("her"));
    var pb = el("div", "im-bubble");
    pb.innerHTML = '<span class="im-typing"><i></i><i></i><i></i></span>';
    pending.appendChild(pb);
    thread.appendChild(pending);
    toBottom(true);

    var bubble = null;
    var acc = "";
    var ts = Date.now();
    lastToolRow = null;
    setBusy(true, "正在输入…");

    ctrl = new AbortController();

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, sid: sid, who: WHO }),
      signal: ctrl.signal
    }).then(function (resp) {
      if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
      var reader = resp.body.getReader();
      var dec = new TextDecoder("utf-8");
      var buf = "";

      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          buf = parseChunk(buf, function (name, data) {
            if (name === "meta") {
              if (data.name) NAME = data.name;
            } else if (name === "tool") {
              if (pending.parentNode) pending.remove();
              var label = TOOL_LABEL[data.name] || ("正在用 " + data.name);
              lastToolRow = addSys(label + "…");
              lastToolRow.classList.add("tool");
              toBottom();
            } else if (name === "delta") {
              if (!bubble) {
                if (pending.parentNode) pending.remove();
                bubble = addRow("her", "", ts, false);
                bubble.parentNode.classList.add("head");
              }
              acc += (data.text || "");
              bubble.innerHTML = bodyHtml(acc) + '<span class="im-caret"></span>';
              toBottom();
            } else if (name === "error") {
              if (pending.parentNode) pending.remove();
              if (lastToolRow && lastToolRow.parentNode) lastToolRow.classList.add("done");
              addErr("没连上：" + (data.message || "未知错误"), lastSent);
              msgs.push({ r: "sys", t: "没连上：" + (data.message || "未知错误"), ts: Date.now(), kind: "err" });
              save();
              toBottom(true);
            } else if (name === "done") {
              if (lastToolRow && lastToolRow.parentNode) {
                lastToolRow.classList.add("done");
                lastToolRow.firstChild.textContent = (lastToolRow.firstChild.textContent || "").replace(/…$/, "");
              }
            }
          });
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (err && err.name === "AbortError") return;
      if (pending.parentNode) pending.remove();
      addErr("请求失败：" + errText(err), lastSent);
      msgs.push({ r: "sys", t: "请求失败：" + errText(err), ts: Date.now(), kind: "err" });
      save();
      toBottom(true);
    }).then(function () {
      if (pending.parentNode) pending.remove();
      if (bubble) bubble.innerHTML = bodyHtml(acc);
      if (acc) {
        msgs.push({ r: "her", t: acc, ts: ts });
        save();
      }
      ctrl = null;
      setBusy(false);
      toBottom();
      /* IM 里光标永远留在输入框，好接着打下一句（触屏不抢焦点，免得弹键盘） */
      if (!("ontouchstart" in window) && !input.disabled) input.focus();
    });
  }

  /* ---------- 事件 ---------- */
  input.addEventListener("input", function () { autosize(); refreshSend(); });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      if (!busy && !sendBtn.disabled) send();
    }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    // 生成中按下去 = 停止（半截回复会留下，不丢）
    if (busy) {
      if (ctrl) { try { ctrl.abort(); } catch (err) {} }
      return;
    }
    send();
  });

  Array.prototype.forEach.call(document.querySelectorAll(".im-chips button"), function (b) {
    b.addEventListener("click", function () { send(b.dataset.say || b.textContent); });
  });

  /* 清空：第一次点是确认态，第二次才真清（不用弹窗抢焦点） */
  var clearArm = 0;
  clearBtn.addEventListener("click", function () {
    if (!clearArm) {
      clearArm = setTimeout(function () {        clearArm = 0;
        clearBtn.classList.remove("arm");
        clearBtn.title = "清空这段对话";
      }, 6000);
      clearBtn.classList.add("arm");
      clearBtn.title = "再点一次就清空";
      return;
    }
    clearTimeout(clearArm);
    clearArm = 0;
    clearBtn.classList.remove("arm");
    clearBtn.title = "清空这段对话";
    if (ctrl) { try { ctrl.abort(); } catch (e) {} }
    msgs = [];
    save();
    fetch("/api/chat/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sid: sid, who: WHO })
    });
    renderAll();
    setBusy(false);
    input.value = "";
    autosize();
    refreshSend();
  });

  /* ---------- 启动 ---------- */
  /* 窄屏时管家列表是横向滚动的，把当前选中的那个滚到中间，别让它藏在屏幕外 */
  (function revealActive() {
    var roster = document.querySelector(".im-roster");
    var on = roster && roster.querySelector(".imc.on");
    if (!roster || !on) return;
    if (roster.scrollWidth <= roster.clientWidth + 4) return;
    var left = on.offsetLeft - (roster.clientWidth - on.offsetWidth) / 2;
    roster.scrollLeft = Math.max(0, left);
  })();

  msgs = load();
  renderAll();
  autosize();
  refreshSend();
  if (!READY) {
    root.classList.add("warn");
    if (stateEl) stateEl.textContent = "未接通";
    input.disabled = true;
    input.placeholder = "模型没接通，先去配 config.json 的 chat 段";
  } else {
    input.focus();
  }
})();
