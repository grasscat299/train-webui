// アプリとしてインストールできるようにするための最小の service worker。
// 画面の殻（HTML・ビルド済みの JS/CSS）だけをキャッシュし、データ（/api・/media）は一切キャッシュしない
// （学習の状態や結果が古いまま出るのを避ける）。オフラインでも画面は開くが、データは取れない。
const CACHE = 'train-webui-shell-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/media/')) return; // 常にネットワーク

  if (req.mode === 'navigate') {
    // HTML は新しい版を優先（ビルドし直した後にすぐ反映される）。繋がらない時だけキャッシュ
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put('/', copy));
          return res;
        })
        .catch(() => caches.match('/')),
    );
    return;
  }
  if (url.pathname.startsWith('/assets/')) {
    // ファイル名にハッシュが入っているので、キャッシュ優先でよい
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })),
    );
  }
});
