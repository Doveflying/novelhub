/* 书萤 · Service Worker
 *
 * 目标很具体：手机上「装到主屏、断网还能看已经读过的章节」。
 * 不做通用离线框架 —— 这个应用的数据全在本地 SQLite，断网时真正缺的
 * 只有静态资源和已经渲染过的页面外壳。
 *
 * 三档策略：
 *   static/    → cache-first（资源带 ?v=hash，文件名一变就是新 URL）
 *   页面 HTML  → network-first，失败回缓存，再失败回离线页
 *   /api/*     → 只走网络。写操作绝不能缓存，读接口交给页面自己处理错误
 */
const VERSION = 'v1';
const SHELL = `firefly-shell-${VERSION}`;
const PAGES = `firefly-pages-${VERSION}`;
const OFFLINE_URL = '/static/offline.html';

const PRECACHE = [
  '/static/style.css',
  '/static/ui.css',
  '/static/otaku.css',
  '/static/app.js',
  '/static/read.js',
  '/static/cast.js',
  '/static/refresh.js',
  '/static/brand/mark.svg',
  '/static/brand/favicon.svg',
  '/static/brand/icon-192.png',
  '/static/brand/icon-512.png',
  '/static/manifest.webmanifest',
  OFFLINE_URL,
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL);
      // 单个资源 404 不该让整个安装失败
      await Promise.all(
        PRECACHE.map((url) =>
          cache.add(new Request(url, { cache: 'reload' })).catch(() => {})
        )
      );
      await self.skipWaiting();
    })()
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith('firefly-') && k !== SHELL && k !== PAGES)
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API：一律走网络，别让缓存把进度写坏
  if (url.pathname.startsWith('/api/')) return;

  // 静态资源：cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(SHELL);
        const hit = await cache.match(req);
        if (hit) {
          // 后台悄悄更新，下次打开就是新的
          fetch(req).then((res) => {
            if (res && res.ok) cache.put(req, res.clone());
          }).catch(() => {});
          return hit;
        }
        try {
          const res = await fetch(req);
          if (res && res.ok) cache.put(req, res.clone());
          return res;
        } catch (err) {
          return new Response('', { status: 504, statusText: 'offline' });
        }
      })()
    );
    return;
  }

  // 页面：network-first，断网回缓存
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      (async () => {
        try {
          const res = await fetch(req);
          if (res && res.ok) {
            const cache = await caches.open(PAGES);
            cache.put(req, res.clone());
          }
          return res;
        } catch (err) {
          const cache = await caches.open(PAGES);
          const hit = await cache.match(req);
          if (hit) return hit;
          const shell = await caches.open(SHELL);
          const off = await shell.match(OFFLINE_URL);
          return off || new Response('离线了，这一页还没缓存过。', {
            status: 503,
            headers: { 'Content-Type': 'text/plain; charset=utf-8' },
          });
        }
      })()
    );
  }
});
