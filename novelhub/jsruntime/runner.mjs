/**
 * Venera 兼容的 JS 源执行器。
 *
 * 用法：node runner.mjs '<json>'
 *   json = { file, action, params, proxy, store }
 *   action: search | info | ep | explore | meta
 *
 * 宿主侧 shim：Network / Convert / UI / APP / ComicSource
 * 说明：本执行器只实现 JSON API 类源所需能力；HTML 源需要的 querySelector 尚未实现。
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import path from 'node:path';
import tls from 'node:tls';
import vm from 'node:vm';
import { URL } from 'node:url';

import { parseHTML } from 'linkedom';

const cfg = JSON.parse(process.argv[2] || '{}');
const proxy = cfg.proxy || null;
const storeFile = cfg.store || null;

/* ---------------- 键值存储 ---------------- */
function readStore() {
  if (!storeFile) return {};
  try {
    return JSON.parse(fs.readFileSync(storeFile, 'utf8'));
  } catch {
    return {};
  }
}
function writeStore(obj) {
  if (!storeFile) return;
  fs.mkdirSync(path.dirname(storeFile), { recursive: true });
  fs.writeFileSync(storeFile, JSON.stringify(obj, null, 2), 'utf8');
}
let store = readStore();

/* ---------------- HTTP（支持 HTTP 代理 CONNECT） ---------------- */
function once({ url, method = 'GET', headers = {}, body = null }) {
  return new Promise((resolve, reject) => {
    // 兼容协议相对地址（//host/path）与裸域名
    if (url.startsWith('//')) url = 'https:' + url;
    else if (!/^https?:\/\//i.test(url)) url = 'https://' + url.replace(/^\/+/, '');
    const u = new URL(url);
    const isHttps = u.protocol === 'https:';
    const port = u.port || (isHttps ? 443 : 80);
    const h = { ...headers, 'accept-encoding': 'identity' };
    const payload =
      body == null
        ? null
        : Buffer.isBuffer(body)
          ? body
          : typeof body === 'object'
            ? Buffer.from(JSON.stringify(body))
            : Buffer.from(String(body));
    if (payload && !Object.keys(h).some((k) => k.toLowerCase() === 'content-length')) {
      h['content-length'] = payload.length;
    }

    const fire = (createConnection) => {
      const mod = isHttps ? https : http;
      const req = mod.request(
        {
          host: u.hostname,
          port,
          path: u.pathname + u.search,
          method,
          headers: h,
          createConnection,
          servername: isHttps ? u.hostname : undefined,
        },
        (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => {
            const buf = Buffer.concat(chunks);
            resolve({
              status: res.statusCode,
              headers: res.headers,
              body: buf.toString('utf8'),
              buf,
            });
          });
        }
      );
      req.setTimeout(40000, () => req.destroy(new Error('timeout')));
      req.on('error', reject);
      if (payload) req.write(payload);
      req.end();
    };

    if (proxy && isHttps) {
      const p = new URL(proxy);
      const cr = http.request({ host: p.hostname, port: p.port, method: 'CONNECT', path: `${u.hostname}:${port}` });
      const guard = setTimeout(() => {
        cr.destroy(new Error('proxy CONNECT timeout'));
        reject(new Error('proxy CONNECT timeout'));
      }, 15000);
      cr.on('connect', (res, socket) => {
        clearTimeout(guard);
        if (res.statusCode !== 200) return reject(new Error(`proxy CONNECT ${res.statusCode}`));
        const t = tls.connect({ socket, servername: u.hostname }, () => fire(() => t));
        t.on('error', reject);
      });
      cr.on('error', (e) => {
        clearTimeout(guard);
        reject(e);
      });
      cr.end();
    } else if (proxy && !isHttps) {
      const p = new URL(proxy);
      const req = http.request(
        { host: p.hostname, port: p.port, path: url, method, headers: { ...h, host: u.hostname } },
        (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => {
            const buf = Buffer.concat(chunks);
            resolve({ status: res.statusCode, headers: res.headers, body: buf.toString('utf8'), buf });
          });
        }
      );
      req.on('error', reject);
      if (payload) req.write(payload);
      req.end();
    } else {
      fire(undefined);
    }
  });
}

async function request(opts) {
  // 自动带上 cookie 罐里的键值
  const cookie = cookieHeader(opts.url);
  if (cookie) opts = { ...opts, headers: { ...(opts.headers || {}), cookie } };
  let res = await once(opts);
  absorbSetCookie(opts.url, res.headers);
  for (let i = 0; i < 5; i++) {
    if (![301, 302, 303, 307, 308].includes(res.status) || !res.headers.location) break;
    const next = new URL(res.headers.location, opts.url).toString();
    if (res.status === 303) opts.method = 'GET';
    res = await once({ ...opts, url: next });
    absorbSetCookie(next, res.headers);
  }
  return res;
}

/* ---------------- 宿主 shim ---------------- */
const Network = {
  get: async (url, headers = {}, extra) => request({ url, method: 'GET', headers }),
  post: async (url, headers = {}, body = '', extra) => request({ url, method: 'POST', headers, body }),
  put: async (url, headers = {}, body = '', extra) => request({ url, method: 'PUT', headers, body }),
  patch: async (url, headers = {}, body = '', extra) => request({ url, method: 'PATCH', headers, body }),
  delete: async (url, headers = {}, extra) => request({ url, method: 'DELETE', headers }),
  sendRequest: async (method, url, headers = {}, body = null, extra) =>
    request({ url, method: (method || 'GET').toUpperCase(), headers, body }),
  /** 同 sendRequest，但 body 给 ArrayBuffer（图片/加密响应要用） */
  fetchBytes: async (method, url, headers = {}, body = null, extra) => {
    const r = await request({ url, method: (method || 'GET').toUpperCase(), headers, body });
    return { status: r.status, headers: r.headers, body: r.buf ?? Buffer.from(r.body, 'utf8') };
  },
  getCookies: (url) => cookiesFor(url),
  setCookies: (url, list) => setCookies(url, list),
  deleteCookies: (url) => cookieJar.delete(hostOf(url)),
  clearCookies: (url) => cookieJar.delete(hostOf(url)),
};

/* ---------------- Cookie 罐（部分源依赖） ---------------- */
const cookieJar = new Map();
const hostOf = (url) => {
  try {
    return new URL(url.startsWith('//') ? 'https:' + url : url).hostname;
  } catch {
    return String(url).replace(/^https?:\/\//, '').split('/')[0];
  }
};
function setCookies(url, list) {
  const host = hostOf(url);
  const jar = cookieJar.get(host) || new Map();
  for (const c of Array.isArray(list) ? list : [list]) {
    const name = c?.name ?? c?.key;
    const value = c?.value ?? c?.val;
    if (name) jar.set(String(name), String(value ?? ''));
  }
  cookieJar.set(host, jar);
}
function cookiesFor(url) {
  const jar = cookieJar.get(hostOf(url));
  if (!jar) return [];
  return [...jar.entries()].map(([name, value]) => ({ name, value, domain: hostOf(url) }));
}
function absorbSetCookie(url, headers) {
  const raw = headers?.['set-cookie'];
  if (!raw) return;
  const host = hostOf(url);
  const jar = cookieJar.get(host) || new Map();
  for (const line of Array.isArray(raw) ? raw : [raw]) {
    const [pair] = String(line).split(';');
    const idx = pair.indexOf('=');
    if (idx > 0) jar.set(pair.slice(0, idx).trim(), pair.slice(idx + 1).trim());
  }
  cookieJar.set(host, jar);
}
function cookieHeader(url) {
  const jar = cookieJar.get(hostOf(url));
  if (!jar || jar.size === 0) return null;
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
}

class Cookie {
  constructor(o = {}) {
    Object.assign(this, o);
  }
}

class Comic {
  constructor(o = {}) {
    Object.assign(this, o);
  }
}

class ComicDetails {
  constructor(o = {}) {
    Object.assign(this, o);
  }
}

class Author {
  constructor(o = {}) {
    Object.assign(this, o);
  }
}

class Comment {
  constructor(o = {}) {
    Object.assign(this, o);
  }
}

/** Venera 的 QuickJS 里也暴露了 fetch（部分源直接用） */
async function fetchShim(input, init = {}) {
  const res = await request({
    url: String(input),
    method: init.method || 'GET',
    headers: init.headers || {},
    body: init.body ?? null,
  });
  const text = res.body;
  return {
    ok: res.status >= 200 && res.status < 300,
    status: res.status,
    headers: res.headers,
    async text() {
      return text;
    },
    async json() {
      return JSON.parse(text);
    },
  };
}

const toBuf = (v) => (Buffer.isBuffer(v) ? v : v instanceof Uint8Array ? Buffer.from(v) : Buffer.from(String(v)));

/* AES：按 key 长度选 aes-128/192/256，mode 见 init.js 的 Convert 契约 */
function aesAlgo(key, mode, blockSize) {
  const bits = { 16: 128, 24: 192, 32: 256 }[key.length];
  if (!bits) throw new Error(`AES key 长度非法：${key.length}`);
  const m = mode === 'cfb' || mode === 'ofb'
    ? (Number(blockSize) === 8 ? 'cfb8' : mode === 'ofb' ? 'ofb' : 'cfb')
    : mode;
  return `aes-${bits}-${m}`;
}
function aesRun(mode, enc, value, key, iv, blockSize) {
  const k = toBuf(key);
  const algo = aesAlgo(k, mode, blockSize);
  const opts = mode === 'ecb' ? {} : { iv: toBuf(iv) };
  if (mode !== 'ecb' && (!opts.iv || opts.iv.length !== 16)) {
    throw new Error(`AES ${mode} 需要 16 字节 iv`);
  }
  const c = enc ? crypto.createCipheriv(algo, k, mode === 'ecb' ? null : opts.iv)
                : crypto.createDecipheriv(algo, k, mode === 'ecb' ? null : opts.iv);
  return Buffer.concat([c.update(toBuf(value)), c.final()]);
}

/* GBK：Node 24 带完整 ICU，TextDecoder 支持 gbk / gb18030 */
let _gbkDec;
function gbkDecoder() {
  if (_gbkDec === undefined) {
    try {
      _gbkDec = new TextDecoder('gbk');
    } catch {
      _gbkDec = null;
    }
  }
  if (!_gbkDec) throw new Error('当前 Node 缺少 GBK 支持（需要完整 ICU）');
  return _gbkDec;
}

const Convert = {
  encodeUtf8: (s) => Buffer.from(String(s), 'utf8'),
  decodeUtf8: (b) => toBuf(b).toString('utf8'),
  encodeGbk: (s) => {
    // Node 没有 TextEncoder('gbk')：退化成 GB18030 子集不可行，
    // 但实际源里 encodeGbk 极少用，这里用 latin1 兜底并提示
    throw new Error('encodeGbk 未实现（Node 无 GBK 编码器）；只有极少数源会用到');
  },
  decodeGbk: (b) => gbkDecoder().decode(toBuf(b)),
  encodeBase64: (v) => toBuf(v).toString('base64'),
  decodeBase64: (s) => Buffer.from(String(s), 'base64'),
  md5: (v) => crypto.createHash('md5').update(toBuf(v)).digest(),
  sha1: (v) => crypto.createHash('sha1').update(toBuf(v)).digest(),
  sha256: (v) => crypto.createHash('sha256').update(toBuf(v)).digest(),
  sha512: (v) => crypto.createHash('sha512').update(toBuf(v)).digest(),
  hexEncode: (v) => (typeof v === 'string' ? Buffer.from(v, 'utf8').toString('hex') : toBuf(v).toString('hex')),
  hexDecode: (s) => Buffer.from(String(s), 'hex'),
  hmacString: (key, data, algo = 'sha256') =>
    crypto.createHmac(String(algo).toLowerCase().replace('-', ''), toBuf(key)).update(toBuf(data)).digest('base64'),
  hmac: (key, data, algo = 'sha256') =>
    crypto.createHmac(String(algo).toLowerCase().replace('-', ''), toBuf(key)).update(toBuf(data)).digest(),

  // --- AES（init.js: encryptAesEcb(value,key) / Cbc(value,key,iv) / Cfb(value,key,iv,blockSize) / Ofb(value,key,blockSize)）
  encryptAesEcb: (v, k) => aesRun('ecb', true, v, k),
  decryptAesEcb: (v, k) => aesRun('ecb', false, v, k),
  encryptAesCbc: (v, k, iv) => aesRun('cbc', true, v, k, iv),
  decryptAesCbc: (v, k, iv) => aesRun('cbc', false, v, k, iv),
  encryptAesCfb: (v, k, iv, bs) => aesRun('cfb', true, v, k, iv, bs),
  decryptAesCfb: (v, k, iv, bs) => aesRun('cfb', false, v, k, iv, bs),
  encryptAesOfb: (v, k, iv, bs) => aesRun('ofb', true, v, k, iv, bs),
  decryptAesOfb: (v, k, iv, bs) => aesRun('ofb', false, v, k, iv, bs),

  /** RSA 私钥解密（PKCS#1 v1.5），供需要解响应签的源用 */
  decryptRsa: (value, key) => {
    const pem = /-----BEGIN/.test(String(key))
      ? String(key)
      : `-----BEGIN PRIVATE KEY-----\n${String(key).replace(/(.{64})/g, '$1\n')}\n-----END PRIVATE KEY-----`;
    return crypto.privateDecrypt(
      { key: pem, padding: crypto.constants.RSA_PKCS1_PADDING },
      toBuf(value)
    );
  },
};

const UI = {
  showMessage: (m) => process.stderr.write(`[UI] ${m}\n`),
  toast: (m) => process.stderr.write(`[UI] ${m}\n`),
  showDialog: (title, content, actions) =>
    process.stderr.write(`[UI dialog] ${title}: ${content}\n`),
  launchUrl: (url) => process.stderr.write(`[UI launch] ${url}\n`),
  showLoading: (onCancel) => 'loading-0',
  cancelLoading: () => {},
  showInputDialog: (title, validator, image) => process.stderr.write(`[UI input] ${title}\n`),
  showSelectDialog: (title, options, initialIndex) => initialIndex ?? 0,
};

/** 日志（init.js: log(level, title, content)） */
function log(level, title, content) {
  process.stderr.write(`[${level || 'info'}] ${title || ''}${content ? ' ' + content : ''}\n`);
}

/* 随机 / UUID / 剪贴板 / compute —— init.js 顶层全局函数 */
function randomInt(min = 0, max = 100) {
  const lo = Math.ceil(Number(min));
  const hi = Math.floor(Number(max));
  return lo + Math.floor(Math.random() * (hi - lo + 1));
}
function randomDouble(min = 0, max = 1) {
  return Number(min) + Math.random() * (Number(max) - Number(min));
}
function createUuid() {
  return crypto.randomUUID();
}
let clipboard = '';
function setClipboard(text) {
  clipboard = String(text ?? '');
}
function getClipboard() {
  return clipboard;
}
/** 把函数跑一遍（App 里是丢进 isolate，这里同线程直调） */
function compute(fn, ...args) {
  return typeof fn === 'function' ? fn(...args) : fn;
}

const APP = { locale: 'zh-CN', version: '1.6.1' };

/* ---------------- DOM（linkedom 实现 Venera 的 HtmlDocument/HtmlElement/HtmlNode） ---------------- */
class HtmlElement {
  constructor(el) {
    this._el = el;
  }
  get text() {
    return this._el.textContent ?? '';
  }
  get attributes() {
    const out = {};
    for (const a of this._el.attributes || []) out[a.name] = a.value;
    return out;
  }
  get children() {
    return [...(this._el.children || [])].map((e) => new HtmlElement(e));
  }
  get nodes() {
    return [...(this._el.childNodes || [])].map((n) => new HtmlNode(n));
  }
  get innerHTML() {
    return this._el.innerHTML ?? '';
  }
  get parent() {
    return this._el.parentElement ? new HtmlElement(this._el.parentElement) : null;
  }
  get classNames() {
    return [...(this._el.classList || [])];
  }
  get id() {
    return this._el.id ?? '';
  }
  get localName() {
    return (this._el.localName || this._el.tagName || '').toLowerCase();
  }
  get previousSibling() {
    const s = this._el.previousElementSibling;
    return s ? new HtmlElement(s) : null;
  }
  get nextSibling() {
    const s = this._el.nextElementSibling;
    return s ? new HtmlElement(s) : null;
  }
  querySelector(q) {
    const e = this._el.querySelector(q);
    return e ? new HtmlElement(e) : null;
  }
  querySelectorAll(q) {
    return [...this._el.querySelectorAll(q)].map((e) => new HtmlElement(e));
  }
  dispose() {}
}

class HtmlNode {
  constructor(node) {
    this._node = node;
  }
  get text() {
    return this._node.textContent ?? '';
  }
  get type() {
    const t = this._node.nodeType;
    return t === 3 ? 'text' : t === 1 ? 'element' : t === 8 ? 'comment' : t === 9 ? 'document' : 'unknown';
  }
  toElement() {
    return this._node.nodeType === 1 ? new HtmlElement(this._node) : null;
  }
}

class HtmlDocument {
  constructor(html) {
    this._doc = parseHTML(String(html ?? '')).document;
  }
  querySelector(q) {
    const e = this._doc.querySelector(q);
    return e ? new HtmlElement(e) : null;
  }
  querySelectorAll(q) {
    return [...this._doc.querySelectorAll(q)].map((e) => new HtmlElement(e));
  }
  getElementById(id) {
    const e = this._doc.getElementById(id);
    return e ? new HtmlElement(e) : null;
  }
  dispose() {}
}

class _Timer {
  constructor(delay, cb) {
    this.delay = delay;
    this.cb = cb;
    this.status = false;
  }
  run() {
    this.status = true;
    const tick = () => {
      if (!this.status) return;
      this.cb();
      this._h = setTimeout(tick, this.delay);
    };
    this._h = setTimeout(tick, this.delay);
  }
  cancel() {
    this.status = false;
    clearTimeout(this._h);
  }
}
const setInterval = (cb, delay) => {
  const t = new _Timer(delay, cb);
  t.run();
  return t;
};
const clearInterval = (t) => t?.cancel?.();

class ComicSource {
  name = '';
  key = '';
  version = '';
  minAppVersion = '';
  url = '';
  translation = {};
  settings = {};

  loadData(k) {
    return store[k] ?? null;
  }
  saveData(k, v) {
    store[k] = v;
    writeStore(store);
  }
  deleteData(k) {
    delete store[k];
    writeStore(store);
  }
  /**
   * 读源设置项。Venera 的语义是「用户没改过就返回 schema 里的 default」，
   * 很多源直接依赖这个默认值（例如 jm 用 loadSetting('apiDomain') 选镜像域名，
   * 返回 null 会让 parseInt 得到 NaN，域名变 undefined 直接崩）。
   */
  loadSetting(k) {
    const ov = store.__settings || {};
    if (Object.prototype.hasOwnProperty.call(ov, k)) return ov[k];
    const decl = this.settings && this.settings[k];
    if (decl && Object.prototype.hasOwnProperty.call(decl, 'default')) return decl.default;
    return null;
  }
  saveSetting(k, v) {
    if (!store.__settings) store.__settings = {};
    store.__settings[k] = v;
    writeStore(store);
  }
  get isLogged() {
    return Boolean(store.token);
  }
  translate(k) {
    return (this.translation[APP.locale] || {})[k] ?? k;
  }
  init() {}
}

/* ---------------- 加载源 ---------------- */
const ctx = {
  ComicSource,
  Network,
  Convert,
  UI,
  APP,
  HtmlDocument,
  HtmlElement,
  HtmlNode,
  _Timer,
  setInterval,
  clearInterval,
  setTimeout,
  clearTimeout,
  fetch: fetchShim,
  log,
  randomInt,
  randomDouble,
  createUuid,
  compute,
  setClipboard,
  getClipboard,
  Comic,
  ComicDetails,
  Author,
  Comment,
  Cookie,
  console: {
    log: (...a) => process.stderr.write(a.join(' ') + '\n'),
    warn: (...a) => process.stderr.write('[warn] ' + a.join(' ') + '\n'),
    error: (...a) => process.stderr.write('[error] ' + a.join(' ') + '\n'),
    info: (...a) => process.stderr.write('[info] ' + a.join(' ') + '\n'),
    debug: () => {},
  },
  Buffer,
  URL,
  encodeURIComponent,
  decodeURIComponent,
  JSON,
  Math,
  Date,
  Promise,
  Map,
  Set,
  Array,
  Object,
  String,
  Number,
  Boolean,
  RegExp,
  Error,
};
ctx.globalThis = ctx;

const code = fs.readFileSync(cfg.file, 'utf8');
const m = code.match(/class\s+(\w+)\s+extends\s+ComicSource/);
if (!m) throw new Error('没有找到 `class X extends ComicSource` 声明');
const className = m[1];
vm.createContext(ctx);
vm.runInContext(`${code}\n;globalThis.__SRC__ = new ${className}();`, ctx, { filename: cfg.file });
const src = ctx.__SRC__;
if (!src) throw new Error('源实例化失败');

/* ---------------- 动作 ---------------- */
const p = cfg.params || {};
let out;
// 注意：源的 init() 通常是 async（jm 要在里面拉域名表并写进 static apiDomains），
// 必须 await，否则后续读到的是未初始化状态（典型症状：apiDomains[0] 为 undefined）。
switch (cfg.action) {
  case 'meta':
    out = { name: src.name, key: src.key, version: src.version, minAppVersion: src.minAppVersion };
    break;
  case 'search':
    await src.init?.();
    out = await src.search.load(p.keyword, p.options ?? [], p.page ?? 1);
    break;
  case 'explore': {
    await src.init?.();
    const tab = (src.explore || [])[p.index ?? 0];
    if (!tab) throw new Error('这个源没有 explore 页面');
    out = await tab.load(p.page ?? 1, p.params);
    break;
  }
  case 'info':
    await src.init?.();
    out = await src.comic.loadInfo(p.id);
    break;
  case 'ep':
    await src.init?.();
    out = await src.comic.loadEp(p.comicId, p.epId);
    break;
  default:
    throw new Error(`未知 action: ${cfg.action}`);
}

process.stdout.write(JSON.stringify({ ok: true, data: out }, (k, v) => (v instanceof Map ? Object.fromEntries(v) : v)));
