/* Anime4K WebGL 超分 —— 从 mpv GLSL 移植
 *
 * 为什么要自己移植：Anime4K 的原始 .glsl 是给 mpv 用的私有方言
 * （//!HOOK / //!BIND / //!SAVE / //!WIDTH 这些指令），
 * 浏览器里既没有 mpv 也没有 libplacebo，不能直接丢给 WebGL 编译。
 *
 * 做法跟 animeko 的 Android 路径一致：
 *   1. 按 //!DESC 把文件切成若干"趟"（pass）
 *   2. 每趟丢掉 //! 开头的指令行，剩下的是纯 GLSL 卷积体
 *   3. 把卷积体塞进自己的 fragment 模板（提供 MAIN_texOff / conv2d_tf_texOff 等宏）
 *   4. 用 ping-pong 帧缓冲按顺序跑，最后一趟输出到屏幕
 *
 * 档位只做 PERFORMANCE（Restore_CNN_S，4 趟）。Upscale_CNN_x2_M 要 9 趟、
 * 每趟多个 3x3 卷积，浏览器里跑 1080p 基本是幻灯片，不划算。
 */
(function () {
  'use strict';

  var NS = window.NH = window.NH || {};

  /* ---------------------------------------------------------------- 着色器 */

  var VERT = [
    'attribute vec2 aPos;',
    'varying vec2 vUv;',
    'void main() {',
    '  vUv = aPos * 0.5 + 0.5;',
    '  gl_Position = vec4(aPos, 0.0, 1.0);',
    '}'
  ].join('\n');

  /* 每趟模板。Anime4K 的卷积体只认一种写法：
       <BIND名>_texOff(offset)   —— 取邻域
       <BIND名>_pos              —— 当前坐标
       MAIN_tex(pos)             —— 取原图（只有融合趟用）
     所以模板按「这一趟绑定谁」生成对应的宏，不要重复定义。 */
  function fragTemplate(macroName) {
    var L = [
      'precision highp float;',
      'varying vec2 vUv;',
      'uniform vec2 uTexel;',
      'uniform sampler2D uTex;',
      'uniform sampler2D uOrig;',
      '#define MAIN_pos vUv',
      '#define MAIN_tex(p) texture2D(uOrig, p)',
      '#define MAIN_texOff(o) texture2D(uOrig, vUv + (o) * uTexel)'
    ];
    /* macro 就是 MAIN 时上面已经定义过，不能再定义一遍（宏重定义在
       部分 GLSL 实现里是硬错误，而且语义也不该有两份）。 */
    if (macroName && macroName !== 'MAIN' && macroName !== 'HOOKED') {
      L.push('#define ' + macroName + '_pos vUv');
      L.push('#define ' + macroName + '_tex(p) texture2D(uTex, p)');
      L.push('#define ' + macroName + '_texOff(o) texture2D(uTex, vUv + (o) * uTexel)');
    }
    L.push('{{BODY}}');
    L.push('void main() { gl_FragColor = hook(); }');
    return L.join('\n');
  }

  /* 把 mpv 的 .glsl 切成趟。
     返回 [{desc, binds:[...], save, comps, body}]，body 已去掉 //! 指令行。 */
  function splitPasses(src) {
    var chunks = src.split(/^\/\/!DESC /m);
    chunks.shift();                       // 第一段是文件头的 MIT 许可证
    var out = [];
    for (var i = 0; i < chunks.length; i++) {
      var lines = chunks[i].split(/\r?\n/);
      var desc = lines.shift();
      var binds = [], save = '', comps = 0, width = '', height = '';
      var body = [];
      for (var j = 0; j < lines.length; j++) {
        var ln = lines[j];
        if (ln.indexOf('//!') === 0) {
          var m = ln.match(/^\/\/!(\w+)\s*(.*)$/);
          if (m) {
            var k = m[1], v = (m[2] || '').trim();
            if (k === 'BIND') binds.push(v);
            else if (k === 'SAVE') save = v;
            else if (k === 'COMPONENTS') comps = parseInt(v, 10) || 0;
            else if (k === 'WIDTH') width = v;
            else if (k === 'HEIGHT') height = v;
          }
          continue;                       // 指令行不进 GLSL
        }
        body.push(ln);
      }
      out.push({
        desc: desc.trim(),
        binds: binds,
        save: save,
        comps: comps,
        width: width,
        height: height,
        body: body.join('\n')
      });
    }
    return out;
  }

  /* ---------------------------------------------------------------- 引擎 */

  function Engine(video, canvas) {
    this.video = video;
    this.canvas = canvas;
    this.gl = null;
    this.ready = false;
    this.enabled = false;
    this.passes = [];
    this.tex = {};                        // 名字 → {tex, fbo, w, h}
    this.raf = 0;
    this.stats = { frames: 0, ms: 0 };
  }

  Engine.prototype.init = function () {
    var gl = this.canvas.getContext('webgl', {
      alpha: false, antialias: false, depth: false, stencil: false,
      preserveDrawingBuffer: false, powerPreference: 'high-performance'
    }) || this.canvas.getContext('experimental-webgl');
    if (!gl) return false;
    this.gl = gl;

    this.progV = this._shader(gl.VERTEX_SHADER, VERT);
    if (!this.progV) return false;

    /* 全屏三角形对（两个三角形） */
    this.quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -1, -1, 1, -1, -1, 1,
      -1, 1, 1, -1, 1, 1
    ]), gl.STATIC_DRAW);

    this.ready = true;
    return true;
  };

  Engine.prototype._shader = function (type, src) {
    var gl = this.gl;
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.warn('[anime4k] shader 编译失败:', gl.getShaderInfoLog(s));
      gl.deleteShader(s);
      return null;
    }
    return s;
  };

  Engine.prototype._program = function (body, macroName) {
    var gl = this.gl;
    var fs = this._shader(gl.FRAGMENT_SHADER,
                          fragTemplate(macroName).replace('{{BODY}}', body));
    if (!fs) return null;
    var p = gl.createProgram();
    gl.attachShader(p, this.progV);
    gl.attachShader(p, fs);
    gl.bindAttribLocation(p, 0, 'aPos');
    gl.linkProgram(p);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      console.warn('[anime4k] program 链接失败:', gl.getProgramInfoLog(p));
      gl.deleteProgram(p);
      return null;
    }
    return {
      prog: p,
      aPos: gl.getAttribLocation(p, 'aPos'),
      uTex: gl.getUniformLocation(p, 'uTex'),
      uOrig: gl.getUniformLocation(p, 'uOrig'),
      uTexel: gl.getUniformLocation(p, 'uTexel')
    };
  };

  /* 载入一份 mpv GLSL，编译成可执行的趟序列 */
  Engine.prototype.load = function (src) {
    var gl = this.gl;
    var raw = splitPasses(src);
    var compiled = [];
    for (var i = 0; i < raw.length; i++) {
      var p = raw[i];
      /* 这一趟读谁：第一个 BIND 就是输入纹理名。
         Anime4K 的卷积体用 <名字>_texOff 取邻域，宏名得跟它对上。 */
      var macro = p.binds.length ? p.binds[0] : 'MAIN';
      /* 最后一趟的 BIND 可能同时有 MAIN 和 conv2d_2_tf，
         但卷积体里既用 MAIN_tex 也用 conv2d_2_tf_texOff，
         模板里 MAIN 恒指向原图，所以 macro 要取非 MAIN 的那个。 */
      for (var b = 0; b < p.binds.length; b++) {
        if (p.binds[b] !== 'MAIN') { macro = p.binds[b]; break; }
      }
      var obj = this._program(p.body, macro);
      if (!obj) return false;
      obj.save = p.save;
      obj.binds = p.binds;
      compiled.push(obj);
    }
    this.passes = compiled;
    return compiled.length > 0;
  };

  Engine.prototype._texture = function (name, w, h) {
    var gl = this.gl;
    var t = this.tex[name];
    if (t && t.w === w && t.h === h) return t;
    if (t) {
      gl.deleteTexture(t.tex);
      gl.deleteFramebuffer(t.fbo);
    }
    var tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA,
                  gl.UNSIGNED_BYTE, null);
    var fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0,
                            gl.TEXTURE_2D, tex, 0);
    t = { tex: tex, fbo: fbo, w: w, h: h };
    this.tex[name] = t;
    return t;
  };

  /* 跑一帧。video 当前帧 → 纹理 → 依次过每趟 → 输出到画布 */
  Engine.prototype.frame = function () {
    var gl = this.gl, v = this.video;
    if (!this.ready || !this.passes.length) return;
    if (v.readyState < 2 || !v.videoWidth || !v.videoHeight) return;

    var vw = v.videoWidth, vh = v.videoHeight;

    /* Restore 只锐化、不放大，所以画布锁定成视频原始尺寸，
       再由 CSS 拉伸到显示区域 —— 这样 GPU 只处理真实像素量。 */
    if (this.canvas.width !== vw || this.canvas.height !== vh) {
      this.canvas.width = vw;
      this.canvas.height = vh;
    }
    var cw = vw, ch = vh;

    var t0 = performance.now();

    gl.viewport(0, 0, cw, ch);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);

    /* 原始帧 */
    var main = this.tex.__main;
    if (!main || main.w !== vw || main.h !== vh) {
      if (main) { gl.deleteTexture(main.tex); gl.deleteFramebuffer(main.fbo); }
      var mt = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, mt);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      main = this.tex.__main = { tex: mt, fbo: null, w: vw, h: vh };
    }
    gl.bindTexture(gl.TEXTURE_2D, main.tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, v);

    var last = this.passes.length - 1;
    for (var i = 0; i <= last; i++) {
      var p = this.passes[i];
      var isLast = (i === last);

      /* 这趟写哪儿：最后一趟直接进画布，其余写 SAVE 指定的中间纹理 */
      var dst = null;
      if (!isLast) {
        /* 尺寸按 //!WIDTH / //!HEIGHT 的声明解析。
           Restore 全是 1:1；Upscale 最后一趟会写 "2 *" 表示放大两倍。 */
        var dw = this._dim(p.width, vw, vh, true);
        var dh = this._dim(p.height, vw, vh, false);
        dst = this._texture(p.save || ('__tmp' + i), dw, dh);
        gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fbo);
        gl.viewport(0, 0, dw, dh);
      } else {
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        gl.viewport(0, 0, cw, ch);
      }

      gl.useProgram(p.prog);
      gl.enableVertexAttribArray(p.aPos);
      gl.vertexAttribPointer(p.aPos, 2, gl.FLOAT, false, 0, 0);

      /* uTex = 这一趟的主输入（第一个非 MAIN 的 BIND，或 MAIN 本身） */
      var srcName = null;
      for (var b = 0; b < p.binds.length; b++) {
        if (p.binds[b] !== 'MAIN') { srcName = p.binds[b]; break; }
      }
      var srcTex = srcName && this.tex[srcName] ? this.tex[srcName].tex : main.tex;
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, srcTex);
      gl.uniform1i(p.uTex, 0);

      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, main.tex);
      gl.uniform1i(p.uOrig, 1);

      /* 邻域步长按「这一趟的输入纹理」算，不是按输出 */
      var stepW = srcName && this.tex[srcName] ? this.tex[srcName].w : vw;
      var stepH = srcName && this.tex[srcName] ? this.tex[srcName].h : vh;
      gl.uniform2f(p.uTexel, 1 / stepW, 1 / stepH);

      gl.drawArrays(gl.TRIANGLES, 0, 6);
    }

    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this.stats.ms = performance.now() - t0;
    this.stats.frames++;
  };

  /* 解析 //!WIDTH / //!HEIGHT 里的尺寸声明。
     形如 "MAIN.w"（跟随主纹理）、"conv2d_tf.w"（跟随某个中间纹理）、
     "conv2d_last_tf.w 2 *"（跟随并放大两倍）。认不出来就退回视频原始尺寸。 */
  Engine.prototype._dim = function (expr, vw, vh, horizontal) {
    var def = horizontal ? vw : vh;
    if (!expr) return def;
    var s = String(expr).trim();

    /* 先看放大倍数：结尾是 "<n> *" */
    var mul = 1;
    var mm = s.match(/([\d.]+)\s*\*\s*$/);
    if (mm) {
      mul = parseFloat(mm[1]) || 1;
      s = s.slice(0, mm.index).trim();
    }

    /* 再看跟随谁：取 ".w" / ".h" 前面的纹理名 */
    var name = null;
    var mn = s.match(/^([\w]+)\.[wh]$/);
    if (mn) name = mn[1];

    var base = def;
    if (name === 'MAIN') {
      base = def;
    } else if (name && this.tex[name]) {
      base = horizontal ? this.tex[name].w : this.tex[name].h;
    }
    return Math.max(1, Math.round(base * mul));
  };

  Engine.prototype.start = function () {
    if (this.raf) return;
    var self = this;
    var loop = function () {
      if (!self.enabled) { self.raf = 0; return; }
      if (!self.video.paused && !self.video.ended) {
        try { self.frame(); } catch (e) {
          console.warn('[anime4k] 帧处理失败，自动关闭:', e);
          self.disable();
          return;
        }
      }
      self.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  };

  Engine.prototype.enable = function () {
    this.enabled = true;
    this.canvas.hidden = false;
    this.video.style.visibility = 'hidden';
    this.start();
  };

  Engine.prototype.disable = function () {
    this.enabled = false;
    this.canvas.hidden = true;
    this.video.style.visibility = '';
  };

  NS.Anime4K = {
    Engine: Engine,
    splitPasses: splitPasses,
    fragTemplate: fragTemplate
  };
})();
