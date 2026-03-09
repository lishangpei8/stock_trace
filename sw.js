const CACHE_NAME = 'stocktrace-v3';
const CDN_ASSETS = [
    'https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Outfit:wght@300;400;500;600;700&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js',
];

// Install: 预缓存 CDN 资源（带版本号，不会变）
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(CDN_ASSETS))
    );
    self.skipWaiting();
});

// Activate: 清除旧缓存
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

// Fetch 策略:
//   - GitHub API / 腾讯搜索 API: 不缓存，直接透传
//   - 同源资源 (HTML/JS/CSS): 网络优先，失败时用缓存
//   - CDN 资源 (fonts/chart.js): 缓存优先，无缓存再请求网络
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // 不干预非 GET 请求
    if (event.request.method !== 'GET') return;

    // 不缓存 API 调用
    if (url.hostname === 'api.github.com' || url.hostname === 'smartbox.gtimg.cn') {
        return;
    }

    // CDN 资源：缓存优先（带版本号，内容不变）
    if (url.hostname.includes('cdnjs') || url.hostname.includes('googleapis') || url.hostname.includes('gstatic')) {
        event.respondWith(
            caches.match(event.request).then(cached => {
                if (cached) return cached;
                return fetch(event.request).then(res => {
                    if (res.ok) {
                        const clone = res.clone();
                        caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
                    }
                    return res;
                });
            })
        );
        return;
    }

    // 同源资源（HTML/JS/CSS/JSON）：网络优先，离线时用缓存
    event.respondWith(
        fetch(event.request).then(res => {
            if (res.ok) {
                const clone = res.clone();
                caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
            }
            return res;
        }).catch(() => caches.match(event.request))
    );
});
