/* 弹幕渲染层：canvas 绘制，支持滚动/顶部/底部、颜色、字号、速度、透明度、屏蔽词。
   数据来自 /api/danmu（弹弹play），按视频 currentTime 调度。
   设计参考 Lanerc / 弹弹play 的参数集，但完全独立实现。 */
(function () {
  'use strict';

  var DEFAULTS = {
    enabled: true,
    opacity: 1.0,          // 整体不透明度
    fontSize: 25,          // 基准字号
    speed: 1.0,            // 滚动速度倍率
    area: 1.0,             // 显示区域（占画面高度的比例）
    bold: true,
    outline: true,         // 描边
    showScroll: true,
    showTop: true,
    showBottom: true,
    blockWords: '',        // 屏蔽词，逗号分隔
    mergeSame: true        // 合并重复弹幕
  };

  var PREF_KEY = 'nh_danmu_prefs_v1';

  function loadPrefs() {
    try {
      var s = localStorage.getItem(PREF_KEY);
      if (!s) return Object.assign({}, DEFAULTS);
      var o = JSON.parse(s);
      return Object.assign({}, DEFAULTS, o || {});
    } catch (e) {
      return Object.assign({}, DEFAULTS);
    }
  }

  function savePrefs(p) {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch (e) {}
  }

  /* ---------------- 弹幕池 ---------------- */
  function Pool(comments) {
    this.all = (comments || []).slice().sort(function (a, b) { return a.time - b.time; });
    this.cursor = 0;
    this.live = [];       // 正在屏幕上跑的
  }

  Pool.prototype.reset = function () {
    this.cursor = 0;
    this.live = [];
  };

  /* 把 [t, t+dt) 区间内的弹幕取出来，追加到 live */
  Pool.prototype.pull = function (t, dt) {
    var out = [];
    while (this.cursor < this.all.length && this.all[this.cursor].time <= t + dt) {
      var c = this.all[this.cursor];
      if (c.time >= t - 0.05) out.push(c);
      this.cursor++;
    }
    return out;
  };

  /* 跳转/回退时重新定位游标 */
  Pool.prototype.seek = function (t) {
    this.live = [];
    var lo = 0, hi = this.all.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (this.all[mid].time < t) lo = mid + 1; else hi = mid;
    }
    this.cursor = lo;
  };

  /* ---------------- 渲染器 ---------------- */
  function Renderer(canvas, prefs) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.prefs = prefs;
    this.tracks = [];       // 每条轨道最后一个弹幕的结束时间
    this.topQueue = [];
    this.bottomQueue = [];
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.resize();
  }

  Renderer.prototype.resize = function () {
    var r = this.cv.getBoundingClientRect();
    var w = Math.max(1, Math.round(r.width));
    var h = Math.max(1, Math.round(r.height));
    this.cv.width = Math.round(w * this.dpr);
    this.cv.height = Math.round(h * this.dpr);
    this.W = w;
    this.H = h;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this._layoutTracks();
  };

  Renderer.prototype._layoutTracks = function () {
    var fs = this.prefs.fontSize;
    var usable = this.H * this.prefs.area;
    var n = Math.max(1, Math.floor(usable / (fs + 6)));
    this.trackCount = n;
    this.trackH = usable / n;
  };

  Renderer.prototype.setPrefs = function (p) {
    this.prefs = p;
    this._layoutTracks();
  };

  /* 屏蔽词 / 类型过滤 */
  Renderer.prototype.accept = function (c) {
    var p = this.prefs;
    if (c.mode === 1 && !p.showScroll) return false;
    if (c.mode === 5 && !p.showTop) return false;
    if (c.mode === 4 && !p.showBottom) return false;
    if (p.blockWords) {
      var words = p.blockWords.split(/[,，\s]+/).filter(Boolean);
      for (var i = 0; i < words.length; i++) {
        if (words[i] && c.text.indexOf(words[i]) >= 0) return false;
      }
    }
    return true;
  };

  /* 加一条弹幕到 live */
  Renderer.prototype.spawn = function (c, now) {
    var p = this.prefs;
    var fs = p.fontSize * (c.size ? c.size / 25 : 1);
    fs = Math.max(12, Math.min(48, fs));
    var text = c.text;

    if (p.mergeSame) {
      // 同文本同轨道短时间内只显示一次，避免刷屏
      for (var i = this.live.length - 1; i >= 0; i--) {
        var l = this.live[i];
        if (l.text === text && now - l.born < 1.5) return;
      }
    }

    var d = {
      text: text,
      mode: c.mode,
      color: c.color || '#ffffff',
      size: fs,
      born: now,
      x: this.W,
      y: 0,
      width: 0
    };

    var ctx = this.ctx;
    ctx.font = this._font(fs);
    d.width = ctx.measureText(text).width;

    if (c.mode === 1) {
      // 滚动：找一条能塞下的轨道
      var track = this._pickTrack(now, d.width);
      if (track < 0) return;   // 满了就丢，不排队（弹弹play 也是这样）
      d.track = track;
      d.y = this.trackH * (track + 1) - 6;
      d.speed = (this.W + d.width) / (9 / Math.max(0.2, p.speed));  // px/s
    } else if (c.mode === 5) {
      d.y = 24 + (this.topQueue.length % Math.max(1, this.trackCount)) * (fs + 6);
      d.topUntil = now + 4.5;
      this.topQueue.push(d);
    } else {
      d.y = this.H - 18 - (this.bottomQueue.length % Math.max(1, this.trackCount)) * (fs + 6);
      d.bottomUntil = now + 4.5;
      this.bottomQueue.push(d);
    }
    this.live.push(d);
  };

  Renderer.prototype._pickTrack = function (now, width) {
    // 轨道里最后一条已经跑出左边 enough 就能复用
    for (var i = 0; i < this.trackCount; i++) {
      var last = null;
      for (var j = this.live.length - 1; j >= 0; j--) {
        if (this.live[j].track === i) { last = this.live[j]; break; }
      }
      if (!last) return i;
      var lastRight = last.x + last.width;
      // 尾巴离开右边界一段距离才允许下一条进来
      if (lastRight < this.W - 30) return i;
    }
    return -1;
  };

  Renderer.prototype._font = function (fs) {
    return (this.prefs.bold ? '700 ' : '') + fs + 'px "PingFang SC","Microsoft YaHei",sans-serif';
  };

  Renderer.prototype.draw = function (dt, now) {
    var ctx = this.ctx;
    var p = this.prefs;
    ctx.clearRect(0, 0, this.W, this.H);
    if (!p.enabled) return;

    ctx.globalAlpha = p.opacity;
    ctx.textBaseline = 'alphabetic';
    ctx.lineJoin = 'round';

    var keep = [];
    for (var i = 0; i < this.live.length; i++) {
      var d = this.live[i];

      if (d.mode === 1) {
        d.x -= d.speed * dt;
        if (d.x + d.width < 0) continue;      // 跑出去了
      } else if (d.mode === 5) {
        if (now > d.topUntil) continue;
      } else {
        if (now > d.bottomUntil) continue;
      }

      ctx.font = this._font(d.size);
      var x = d.mode === 1 ? d.x : (this.W - d.width) / 2;

      if (p.outline) {
        ctx.strokeStyle = 'rgba(0,0,0,0.75)';
        ctx.lineWidth = Math.max(1.5, d.size / 12);
        ctx.strokeText(d.text, x, d.y);
      }
      ctx.fillStyle = d.color;
      ctx.fillText(d.text, x, d.y);

      keep.push(d);
    }
    this.live = keep;
    ctx.globalAlpha = 1;
  };

  /* ---------------- 控制器 ---------------- */
  function Danmaku(video, canvas, opts) {
    this.video = video;
    this.prefs = loadPrefs();
    this.renderer = new Renderer(canvas, this.prefs);
    this.pool = new Pool([]);
    this.lastT = video.currentTime || 0;
    this.now = 0;             // 渲染用时钟（秒，单调递增）
    this.raf = null;
    this.count = 0;
    this.onStatus = (opts && opts.onStatus) || function () {};

    var self = this;
    window.addEventListener('resize', function () {
      self.renderer.resize();
    });
    // 跳转后重新定位
    video.addEventListener('seeking', function () {
      self.pool.seek(video.currentTime);
      self.lastT = video.currentTime;
    });
    video.addEventListener('play', function () { self.start(); });
    video.addEventListener('pause', function () { self.stop(); });
    this.start();
  }

  Danmaku.prototype.start = function () {
    if (this.raf) return;
    var self = this;
    var prev = performance.now();
    function loop(t) {
      var dt = Math.min(0.1, (t - prev) / 1000);
      prev = t;
      self.now += dt;
      self.tick(dt);
      self.raf = requestAnimationFrame(loop);
    }
    this.raf = requestAnimationFrame(loop);
  };

  Danmaku.prototype.stop = function () {
    if (this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
    }
  };

  Danmaku.prototype.tick = function (dt) {
    var v = this.video;
    if (!v.paused && !v.ended) {
      var t = v.currentTime;
      // 正常播放才推进弹幕；拖进度条后由 seeking 重置
      if (t >= this.lastT && t - this.lastT < 1.0) {
        var fresh = this.pool.pull(t, 0.25);
        for (var i = 0; i < fresh.length; i++) {
          if (this.renderer.accept(fresh[i])) {
            this.renderer.spawn(fresh[i], this.now);
          }
        }
      }
      this.lastT = t;
    }
    this.renderer.draw(dt, this.now);
  };

  Danmaku.prototype.load = function (comments) {
    this.pool = new Pool(comments);
    this.count = (comments || []).length;
    this.pool.seek(this.video.currentTime || 0);
    this.lastT = this.video.currentTime || 0;
    this.renderer.live = [];
    this.onStatus(this.count ? ('弹幕 ' + this.count + ' 条') : '无弹幕');
  };

  Danmaku.prototype.setPrefs = function (patch) {
    Object.assign(this.prefs, patch);
    savePrefs(this.prefs);
    this.renderer.setPrefs(this.prefs);
  };

  window.NovelDanmaku = { Danmaku: Danmaku, DEFAULTS: DEFAULTS };
})();
