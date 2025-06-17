const CACHE_NAME = 'barbearia-painel-cache-v1';
const OFFLINE_URL = 'offline.html';  

 
const URLS_TO_CACHE = [
  '/',
  '/static/offline.html'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Cache aberto');
        return cache.addAll(URLS_TO_CACHE);
      })
  );
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
      
        if (response) {
          return response;
        }
       
        return fetch(event.request).catch(() => {
       
          return caches.match(OFFLINE_URL);
        });
      })
  );
});
