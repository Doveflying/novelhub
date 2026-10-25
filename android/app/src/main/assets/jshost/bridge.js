/*
 * 书萤 · Venera JS 源的宿主契约（WebView 内实现）
 *
 * 这个文件是 refs/venera/init.js 的等价实现：Venera 用 Dart 侧 sendMessage
 * 回调宿主，这里换成 @JavascriptInterface 同步回调到 Python。
 *
 * 为什么能同步：Android 的 @JavascriptInterface 方法在 JS 调用时是阻塞的，
 * JS 线程会等到 Java 返回。Convert.* / Network.* 在 init.js 里本来就是
 * 「调用即得结果」的语义（await 一个非 Promise 值会立刻 resolve），
 * 所以同步桥刚好对上，不需要额外的 Promise 编排。
 *
 * 与 Node 版 runner.mjs 的差别：HTML 用 WebView 自带的 DOM（DOMParser），
 * 比 linkedom 更完整；加密/HTTP/存储全部落到 Python 侧，和桌面端同一条链路。
 */

(function () {
  'use strict';

  /* ------------------------------------------------------------------ 桥 */
  var bridge = window.__veneraBridge;
  var JOB = null;

  function host(req) {
    req.job = JOB;
    var raw = bridge.host(JSON.stringify(req));
    return JSON.parse(raw);
  }

  function hostOrThrow(req) {
    var r = host(req);
    if (r && r.error) throw new Error(r.error);
    return r;
  }

  /* -------------------------------------------------------- ArrayBuffer 工具 */
  function b64ToBuf(b64) {
    if (!b64) return new ArrayBuffer(0);
    var bin = atob(b64);
    var n = bin.length;
    var out = new Uint8Array(n);
    for (var i = 0; i < n; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  function bufToB64(buf) {
    if (buf == null) return '';
    var u8 = buf instanceof ArrayBuffer ? new Uint8Array(buf) : new Uint8Array(buf.buffer || buf);
    var s = '';
    var CH = 0x8000;
    for (var i = 0; i < u8.length; i += CH) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
    }
    return btoa(s);
  }

  function toBuf(v) {
    if (v == null) return new ArrayBuffer(0);
    if (v instanceof ArrayBuffer) return v;
    if (ArrayBuffer.isView(v)) return v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength);
    if (typeof v === 'string') {
      // 字符串按 UTF-8 编码（Venera 里 ArrayBuffer 入参也可能是明文）
      var e = new TextEncoder().encode(v);
      return e.buffer;
    }
    return new TextEncoder().encode(String(v)).buffer;
  }

  /* -------------------------------------------------------------- Convert */
  var Convert = {
    encodeUtf8: function (str) {
      return new TextEncoder().encode(str == null ? '' : String(str)).buffer;
    },
    decodeUtf8: function (value) {
      return hostOrThrow({ op: 'convert', type: 'utf8', isEncode: false, value: bufToB64(value) }).value;
    },
    encodeGbk: function (str) {
      var r = hostOrThrow({ op: 'convert', type: 'gbk', isEncode: true, value: String(str == null ? '' : str) });
      return b64ToBuf(r.b64);
    },
    decodeGbk: function (value) {
      return hostOrThrow({ op: 'convert', type: 'gbk', isEncode: false, value: bufToB64(value) }).value;
    },
    encodeBase64: function (value) {
      return hostOrThrow({ op: 'convert', type: 'base64', isEncode: true, value: bufToB64(value) }).value;
    },
    decodeBase64: function (value) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'base64', isEncode: false, value: String(value) }).b64);
    },
    md5: function (value) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'md5', value: bufToB64(value) }).b64);
    },
    sha1: function (value) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'sha1', value: bufToB64(value) }).b64);
    },
    sha256: function (value) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'sha256', value: bufToB64(value) }).b64);
    },
    sha512: function (value) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'sha512', value: bufToB64(value) }).b64);
    },
    hmac: function (key, value, hash) {
      return b64ToBuf(
        hostOrThrow({
          op: 'convert', type: 'hmac', value: bufToB64(value),
          key: bufToB64(key), hash: hash || 'sha256', isEncode: true,
        }).b64
      );
    },
    hmacString: function (key, value, hash) {
      return hostOrThrow({
        op: 'convert', type: 'hmac', value: bufToB64(value),
        key: bufToB64(key), hash: hash || 'sha256', isEncode: true, isString: true,
      }).value;
    },
    encryptAesEcb: function (v, k) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-ecb', value: bufToB64(v), key: bufToB64(k), isEncode: true }).b64);
    },
    decryptAesEcb: function (v, k) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-ecb', value: bufToB64(v), key: bufToB64(k), isEncode: false }).b64);
    },
    encryptAesCbc: function (v, k, iv) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-cbc', value: bufToB64(v), key: bufToB64(k), iv: bufToB64(iv), isEncode: true }).b64);
    },
    decryptAesCbc: function (v, k, iv) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-cbc', value: bufToB64(v), key: bufToB64(k), iv: bufToB64(iv), isEncode: false }).b64);
    },
    encryptAesCfb: function (v, k, iv, bs) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-cfb', value: bufToB64(v), key: bufToB64(k), iv: bufToB64(iv), blockSize: bs, isEncode: true }).b64);
    },
    decryptAesCfb: function (v, k, iv, bs) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-cfb', value: bufToB64(v), key: bufToB64(k), iv: bufToB64(iv), blockSize: bs, isEncode: false }).b64);
    },
    encryptAesOfb: function (v, k, iv, bs) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-ofb', value: bufToB64(v), key: bufToB64(k), iv: bufToB64(iv), blockSize: bs, isEncode: true }).b64);
    },
    decryptAesOfb: function (v, k, iv, bs) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'aes-ofb', value: bufToB64(v), key: bufToB64(k), iv: bufToB64(iv), blockSize: bs, isEncode: false }).b64);
    },
    decryptRsa: function (v, key) {
      return b64ToBuf(hostOrThrow({ op: 'convert', type: 'rsa', value: bufToB64(v), key: String(key), isEncode: false }).b64);
    },
    hexEncode: function (bytes) {
      var view = new Uint8Array(toBuf(bytes));
      var s = '';
      for (var i = 0; i < view.length; i++) s += view[i].toString(16).padStart(2, '0');
      return s;
    },
    hexDecode: function (hex) {
      var h = String(hex || '');
      var n = Math.floor(h.length / 2);
      var out = new Uint8Array(n);
      for (var i = 0; i < n; i++) out[i] = parseInt(h.substr(i * 2, 2), 16);
      return out.buffer;
    },
  };

  /* -------------------------------------------------------------- Cookie 罐 */
  var jar = Object.create(null);

  function hostOf(url) {
    try {
      var s = String(url);
      if (s.indexOf('//') === 0) s = 'https:' + s;
      return new URL(s).hostname;
    } catch (e) {
      return String(url).replace(/^https?:\/\//, '').split('/')[0];
    }
  }

  function absorb(url, headers) {
    var raw = headers && headers['set-cookie'];
    if (!raw) return;
    var h = hostOf(url);
    var m = jar[h] || (jar[h] = Object.create(null));
    (Array.isArray(raw) ? raw : [raw]).forEach(function (line) {
      var pair = String(line).split(';')[0];
      var i = pair.indexOf('=');
      if (i > 0) m[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
    });
  }

  function cookieHeader(url) {
    var m = jar[hostOf(url)];
    if (!m) return null;
    var kv = Object.keys(m).map(function (k) { return k + '=' + m[k]; });
    return kv.length ? kv.join('; ') : null;
  }

  /* ---------------------------------------------------------------- Network */
  function doRequest(method, url, headers, data, wantBytes) {
    var r = hostOrThrow({
      op: 'http',
      method: String(method || 'GET').toUpperCase(),
      url: String(url),
      headers: headers || {},
      data: data == null ? null : (typeof data === 'string' ? data : { b64: bufToB64(toBuf(data)) }),
      bytes: !!wantBytes,
    });
    absorb(url, r.headers);
    return {
      status: r.status,
      headers: r.headers || {},
      body: wantBytes ? b64ToBuf(r.b64) : r.body,
      _b64: r.b64,
    };
  }

  var Network = {
    sendRequest: function (method, url, headers, data, extra) {
      return doRequest(method, url, headers, data, false);
    },
    fetchBytes: function (method, url, headers, data, extra) {
      return doRequest(method, url, headers, data, true);
    },
    get: function (url, headers, extra) { return Network.sendRequest('GET', url, headers, null, extra); },
    post: function (url, headers, data, extra) { return Network.sendRequest('POST', url, headers, data, extra); },
    put: function (url, headers, data, extra) { return Network.sendRequest('PUT', url, headers, data, extra); },
    patch: function (url, headers, data, extra) { return Network.sendRequest('PATCH', url, headers, data, extra); },
    delete: function (url, headers, extra) { return Network.sendRequest('DELETE', url, headers, null, extra); },
    setCookies: function (url, list) {
      var h = hostOf(url);
      var m = jar[h] || (jar[h] = Object.create(null));
      (Array.isArray(list) ? list : [list]).forEach(function (c) {
        var name = c && (c.name != null ? c.name : c.key);
        var value = c && (c.value != null ? c.value : c.val);
        if (name != null) m[String(name)] = String(value == null ? '' : value);
      });
    },
    getCookies: function (url) {
      var h = hostOf(url);
      var m = jar[h] || {};
      return Object.keys(m).map(function (k) { return { name: k, value: m[k], domain: h }; });
    },
    deleteCookies: function (url) { delete jar[hostOf(url)]; },
    clearCookies: function (url) { delete jar[hostOf(url)]; },
  };

  /* ------------------------------------------------------- HTML（真 DOM） */
  var docRegistry = Object.create(null);
  var docSeq = 0;

  function HtmlDocument(html) {
    this.key = docSeq++;
    var text = html == null ? '' : String(html);
    var parsed = new DOMParser().parseFromString(text, 'text/html');
    docRegistry[this.key] = parsed;
  }
  HtmlDocument.prototype._doc = function () { return docRegistry[this.key]; };
  HtmlDocument.prototype.querySelector = function (q) {
    var e = this._doc().querySelector(q);
    return e ? new HtmlElement(e) : null;
  };
  HtmlDocument.prototype.querySelectorAll = function (q) {
    return Array.prototype.slice.call(this._doc().querySelectorAll(q)).map(function (e) {
      return new HtmlElement(e);
    });
  };
  HtmlDocument.prototype.getElementById = function (id) {
    var e = this._doc().getElementById(id);
    return e ? new HtmlElement(e) : null;
  };
  HtmlDocument.prototype.dispose = function () { delete docRegistry[this.key]; };

  function HtmlElement(el) { this._el = el; }

  Object.defineProperty(HtmlElement.prototype, 'text', {
    get: function () { return this._el.textContent == null ? '' : this._el.textContent; },
  });
  Object.defineProperty(HtmlElement.prototype, 'attributes', {
    get: function () {
      var out = {};
      var list = this._el.attributes || [];
      for (var i = 0; i < list.length; i++) out[list[i].name] = list[i].value;
      return out;
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'children', {
    get: function () {
      return Array.prototype.slice.call(this._el.children || []).map(function (e) {
        return new HtmlElement(e);
      });
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'nodes', {
    get: function () {
      return Array.prototype.slice.call(this._el.childNodes || []).map(function (n) {
        return new HtmlNode(n);
      });
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'innerHTML', {
    get: function () { return this._el.innerHTML == null ? '' : this._el.innerHTML; },
  });
  Object.defineProperty(HtmlElement.prototype, 'parent', {
    get: function () {
      var p = this._el.parentElement;
      return p ? new HtmlElement(p) : null;
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'classNames', {
    get: function () { return Array.prototype.slice.call(this._el.classList || []); },
  });
  Object.defineProperty(HtmlElement.prototype, 'id', {
    get: function () { return this._el.id || ''; },
  });
  Object.defineProperty(HtmlElement.prototype, 'localName', {
    get: function () { return (this._el.localName || this._el.tagName || '').toLowerCase(); },
  });
  Object.defineProperty(HtmlElement.prototype, 'previousElementSibling', {
    get: function () {
      var s = this._el.previousElementSibling;
      return s ? new HtmlElement(s) : null;
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'previousSibling', {
    get: function () {
      var s = this._el.previousElementSibling;
      return s ? new HtmlElement(s) : null;
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'nextElementSibling', {
    get: function () {
      var s = this._el.nextElementSibling;
      return s ? new HtmlElement(s) : null;
    },
  });
  Object.defineProperty(HtmlElement.prototype, 'nextSibling', {
    get: function () {
      var s = this._el.nextElementSibling;
      return s ? new HtmlElement(s) : null;
    },
  });
  HtmlElement.prototype.querySelector = function (q) {
    var e = this._el.querySelector(q);
    return e ? new HtmlElement(e) : null;
  };
  HtmlElement.prototype.querySelectorAll = function (q) {
    return Array.prototype.slice.call(this._el.querySelectorAll(q)).map(function (e) {
      return new HtmlElement(e);
    });
  };
  HtmlElement.prototype.dispose = function () {};

  function HtmlNode(node) { this._node = node; }
  Object.defineProperty(HtmlNode.prototype, 'text', {
    get: function () { return this._node.textContent == null ? '' : this._node.textContent; },
  });
  Object.defineProperty(HtmlNode.prototype, 'type', {
    get: function () {
      var t = this._node.nodeType;
      return t === 3 ? 'text' : t === 1 ? 'element' : t === 8 ? 'comment' : t === 9 ? 'document' : 'unknown';
    },
  });
  HtmlNode.prototype.toElement = function () {
    return this._node.nodeType === 1 ? new HtmlElement(this._node) : null;
  };

  /* ------------------------------------------------------------------- 杂项 */
  function log(level, title, content) {
    host({ op: 'log', level: level, title: title, content: content });
  }

  function randomInt(min, max) {
    var lo = Math.ceil(Number(min));
    var hi = Math.floor(Number(max));
    return lo + Math.floor(Math.random() * (hi - lo + 1));
  }

  function randomDouble(min, max) {
    return Number(min) + Math.random() * (Number(max) - Number(min));
  }

  function createUuid() {
    if (crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  var clipboard = '';
  function setClipboard(t) { clipboard = String(t == null ? '' : t); }
  function getClipboard() { return clipboard; }

  /**
   * 在隔离作用域里跑一段脚本并调用它导出的函数。
   *
   * Venera 的语义：脚本最后一行是裸表达式（例如 `onResponse;`），
   * 其完成值就是入口函数。间接 eval 的完成值正好等于最后一条语句的值，
   * 所以这里用 (0, eval) 就能拿到，不用硬编码函数名。
   */
  function compute(script, ...args) {
    var fn = (0, eval)(String(script));
    if (typeof fn === 'function') return fn.apply(null, args);
    return fn;
  }

  var UI = {
    showMessage: function (m) { log('info', 'UI', m); },
    showDialog: function (title, content, actions) {
      log('info', 'UI dialog', title + ': ' + content);
      // 无头环境：直接跑第一个回调，等价于用户点了第一个按钮
      if (Array.isArray(actions) && actions[0] && typeof actions[0].callback === 'function') {
        try { actions[0].callback(); } catch (e) { log('error', 'UI dialog cb', String(e)); }
      }
    },
    launchUrl: function (url) { log('info', 'UI launch', url); },
    showLoading: function (onCancel) { return 0; },
    cancelLoading: function (id) {},
    showInputDialog: function (title) { log('info', 'UI input', title); return null; },
    showSelectDialog: function (title, options, initialIndex) {
      return initialIndex == null ? 0 : initialIndex;
    },
  };

  var APP = {
    get version() { return '1.6.1'; },
    get locale() { return 'zh-CN'; },
    get platform() { return 'android'; },
  };

  function Comic(o) { Object.assign(this, o || {}); }
  function ComicDetails(o) { Object.assign(this, o || {}); }
  function Author(o) { Object.assign(this, o || {}); }
  function Comment(o) { Object.assign(this, o || {}); }
  function Cookie(o) { Object.assign(this, o || {}); }

  /* --------------------------------------------------------------- ComicSource */
  function ComicSource() {}

  ComicSource.prototype.loadData = function (k) {
    return hostOrThrow({ op: 'store', fn: 'load', key: k }).value;
  };
  ComicSource.prototype.saveData = function (k, v) {
    hostOrThrow({ op: 'store', fn: 'save', key: k, value: v });
  };
  ComicSource.prototype.deleteData = function (k) {
    hostOrThrow({ op: 'store', fn: 'delete', key: k });
  };
  ComicSource.prototype.loadSetting = function (k) {
    var decl = this.settings && this.settings[k];
    var dflt = decl && Object.prototype.hasOwnProperty.call(decl, 'default') ? decl.default : null;
    return hostOrThrow({ op: 'setting', key: k, default: dflt }).value;
  };
  ComicSource.prototype.saveSetting = function (k, v) {
    hostOrThrow({ op: 'store', fn: 'save', key: '__settings__' + k, value: v });
  };
  Object.defineProperty(ComicSource.prototype, 'isLogged', {
    get: function () { return !!hostOrThrow({ op: 'store', fn: 'isLogged' }).value; },
  });
  ComicSource.prototype.translate = function (k) {
    var tr = this.translation && this.translation[APP.locale];
    return (tr && tr[k] != null) ? tr[k] : k;
  };
  ComicSource.prototype.init = function () {};

  /* --------------------------------------------------------------- 对外暴露 */
  var ctx = {
    Convert: Convert,
    Network: Network,
    UI: UI,
    APP: APP,
    HtmlDocument: HtmlDocument,
    HtmlElement: HtmlElement,
    HtmlNode: HtmlNode,
    ComicSource: ComicSource,
    Comic: Comic,
    ComicDetails: ComicDetails,
    Author: Author,
    Comment: Comment,
    Cookie: Cookie,
    log: log,
    console: {
      log: function () { log('info', 'JS Console', Array.prototype.join.call(arguments, ' ')); },
      warn: function () { log('warning', 'JS Console', Array.prototype.join.call(arguments, ' ')); },
      error: function () { log('error', 'JS Console', Array.prototype.join.call(arguments, ' ')); },
      info: function () { log('info', 'JS Console', Array.prototype.join.call(arguments, ' ')); },
      debug: function () {},
    },
    randomInt: randomInt,
    randomDouble: randomDouble,
    createUuid: createUuid,
    compute: compute,
    setClipboard: setClipboard,
    getClipboard: getClipboard,
    atob: atob,
    btoa: btoa,
    TextEncoder: TextEncoder,
    TextDecoder: TextDecoder,
    Uint8Array: Uint8Array,
    Int8Array: Int8Array,
    Uint16Array: Uint16Array,
    Int32Array: Int32Array,
    Uint32Array: Uint32Array,
    Float32Array: Float32Array,
    Float64Array: Float64Array,
    DataView: DataView,
    ArrayBuffer: ArrayBuffer,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    setInterval: setInterval,
    clearInterval: clearInterval,
    URL: URL,
    URLSearchParams: URLSearchParams,
    encodeURIComponent: encodeURIComponent,
    decodeURIComponent: decodeURIComponent,
  };
  ctx.globalThis = ctx;

  Object.keys(ctx).forEach(function (k) {
    if (!(k in window)) window[k] = ctx[k];
    else if (k === 'console') window.console = ctx.console;
  });

  /* ---------------------------------------------------------------- 执行入口 */
  function findClassName(code) {
    var m = code.match(/class\s+(\w+)\s+extends\s+ComicSource/);
    if (!m) throw new Error('没有找到 `class X extends ComicSource` 声明');
    return m[1];
  }

  /**
   * 把源源码放进一个作用域里执行，作用域的原型链指向 ctx。
   *
   * 关键点：源里直接写 `class X extends ComicSource`，而 ComicSource 是宿主
   * 注入的全局。用 new Function 的话作用域是真正的全局对象，拿不到 ctx 里的
   * 绑定，会报 "ComicSource is not defined"。所以这里用 with 把 ctx 套在
   * 外层——with 的变量查找优先命中 ctx，全局兜底。
   *
   * 不能用 vm / 独立 realm：WebView 里没有那个 API，而且源需要和宿主共享
   * ArrayBuffer / Promise 的身份（Convert.* 返回的 buffer 要能被源的
   * new Uint8Array() 直接吃）。
   */
  function evalInCtx(code) {
    var factory = new Function(
      '__ctx__',
      'with (__ctx__) {\n' + code + '\n;return new (' + findClassName(code) + ')();\n}'
    );
    return factory.call(ctx, ctx);
  }

  window.__veneraRun = function (payload) {
    JOB = payload.job;
    var done = false;
    function finish(obj) {
      if (done) return;
      done = true;
      try {
        bridge.done(payload.job, JSON.stringify(obj));
      } catch (e) {
        /* 桥已断，忽略 */
      }
    }

    // 整体包在 Promise 里，源里的 async/await 才能正常跑；
    // 但桥是同步的，所以实际是「一个微任务队列跑到底」。
    Promise.resolve()
      .then(function () {
        return evalInCtx(payload.code);
      })
      .then(function (src) {
        var p = payload.params || {};
        switch (payload.action) {
          case 'meta':
            return {
              name: src.name, key: src.key,
              version: src.version, minAppVersion: src.minAppVersion,
            };
          case 'search':
            return Promise.resolve(src.init && src.init()).then(function () {
              return src.search.load(p.keyword, p.options || [], p.page || 1);
            });
          case 'explore':
            return Promise.resolve(src.init && src.init()).then(function () {
              var tab = (src.explore || [])[p.index || 0];
              if (!tab) throw new Error('这个源没有 explore 页面');
              return tab.load(p.page || 1, p.params);
            });
          case 'info':
            return Promise.resolve(src.init && src.init()).then(function () {
              return src.comic.loadInfo(p.id);
            });
          case 'ep':
            return Promise.resolve(src.init && src.init()).then(function () {
              return src.comic.loadEp(p.comicId, p.epId);
            });
          default:
            throw new Error('未知 action: ' + payload.action);
        }
      })
      .then(function (data) {
        finish({ ok: true, data: serialize(data) });
      })
      .catch(function (err) {
        finish({ ok: false, error: String((err && err.message) || err) });
      });
  };

  /** Map / Set / 循环引用都要能过 JSON 桥 */
  function serialize(v) {
    var seen = new WeakSet();
    return JSON.parse(JSON.stringify(v, function (k, val) {
      if (val instanceof Map) return Object.fromEntries(val);
      if (val instanceof Set) return Array.from(val);
      if (val && typeof val === 'object') {
        if (seen.has(val)) return undefined;
        seen.add(val);
      }
      return val;
    }));
  }

  window.__veneraReady = true;
})();
