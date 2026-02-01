import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA, type VitePWAOptions } from 'vite-plugin-pwa'
import { thirdPartyLicensesPlugin } from './build/thirdPartyLicenses'
import { matchTileRequest } from './src/utils/mapTiles'

// Dev-only kill-switch for stale production service workers.
//
// A browser that once visited the production-mode local stack keeps a workbox
// SW registered for `http://localhost/`. Against the dev stack it serves an old
// app shell from precache and asks for build-hashed assets the dev server lacks.
// It never heals itself: its update check fetches `/sw.js`, which in dev hits
// Vite's SPA fallback and returns `text/html`, and a non-JS response fails the
// check.
//
// A self-destroying script at exactly `/sw.js`, the URL the stale registration
// polls, heals the browser on its next load. vite-plugin-pwa's `devOptions`
// cannot do this, because `selfDestroying` makes it serve no dev SW at all.
//
// `apply: 'serve'` keeps this out of every production build.
function devServiceWorkerKillSwitch(): Plugin {
  const script = `// Dev-only self-destroying service worker — see vite.config.ts.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) await caches.delete(key);
    await self.registration.unregister();
    // Reload open tabs so they drop the stale precached shell immediately.
    for (const client of await self.clients.matchAll({ type: 'window' })) {
      client.navigate(client.url);
    }
  })());
});
`
  return {
    name: 'clavis:dev-sw-kill-switch',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/sw.js', (_req, res) => {
        res.setHeader('Content-Type', 'application/javascript')
        res.setHeader('Cache-Control', 'no-store')
        res.end(script)
      })
    },
  }
}

// The service worker's runtime caching rules, in order. A private answer must
// never land in a device cache. Workbox takes the FIRST rule that matches, so
// the order is the privacy boundary: every NetworkOnly rule for private answers
// stands before any rule that stores, the navigation rule included, so a
// direct visit or a CSV download is not stored either.
// src/utils/apiCache.test.ts checks the order.
export type RuntimeCachingRule = NonNullable<
  NonNullable<VitePWAOptions['workbox']>['runtimeCaching']
>[number]

export const runtimeCaching: RuntimeCachingRule[] = [
  {
    // The organization log and its CSV export carry names, e-mail and
    // IP addresses, and the export can be large.
    urlPattern: /^https?:\/\/[^/]+\/api\/orgs\/[^/]+\/admin\/logs(?:\?.*)?$/i,
    handler: 'NetworkOnly'
  },
  {
    // The operator console's answers (accounts, organizations, audit
    // log, settings) are private staff data.
    urlPattern: /^https?:\/\/[^/]+\/api\/instance\/.*$/i,
    handler: 'NetworkOnly'
  },
  {
    // User management, invitations, the admin area and the org-level
    // analytics carry names, e-mail addresses and IPs. Only the paths
    // directly under /api/orgs/{org}/ count: run analytics
    // (/api/orgs/{org}/runs/{run}/analytics) belongs to a run and stays
    // on the generic /api/ rule below.
    urlPattern: /^https?:\/\/[^/]+\/api\/orgs\/[^/]+\/(?:users|invitations|admin|analytics)(?:\/.*)?(?:\?.*)?$/i,
    handler: 'NetworkOnly'
  },
  {
    // A stale `200 /api/auth/me` would make the SPA render a signed-in
    // shell for an expired session, with errors everywhere. A direct
    // visit of /api/auth/me is a navigation request, so this stands
    // before the navigation rule.
    urlPattern: /^https?:\/\/.*\/api\/auth\/.*/i,
    handler: 'NetworkOnly'
  },
  {
    // Online navigations load the app shell. Offline ones get the cached
    // shell, or else the offline page.
    urlPattern: ({ request }) => request.mode === 'navigate',
    handler: 'NetworkFirst',
    options: {
      cacheName: 'pages-cache',
      networkTimeoutSeconds: 10,
      cacheableResponse: {
        statuses: [0, 200]
      },
      precacheFallback: {
        fallbackURL: '/offline.html'
      }
    }
  },
  {
    // The feedback bar polls the current test step and posts notes. A
    // cached step would show the tester a stale card, and the bar keeps
    // its own outbox for notes sent offline.
    urlPattern: /^https?:\/\/.*\/api\/qa\/.*/i,
    handler: 'NetworkOnly'
  },
  {
    // The cache answers only when the network fails, that is offline,
    // where the offline banner explains old data. A slow server is
    // waited for: with a 10 s timeout, a loaded stand would show stale
    // runs and "Noch keine Mitteilungen" without any hint.
    urlPattern: /^https?:\/\/.*\/api\/.*/i,
    handler: 'NetworkFirst',
    options: {
      cacheName: 'api-cache',
      expiration: {
        maxEntries: 50,
        maxAgeSeconds: 60 * 60 * 24 // 24 hours
      },
      cacheableResponse: {
        statuses: [0, 200]
      }
    }
  },
  {
    // The lazy staff, operator and content chunks are not precached and
    // load on first navigation. Their filenames are content hashes, so a
    // cache hit is always the right version and CacheFirst is safe.
    urlPattern: /\/assets\/.*\.js$/i,
    handler: 'CacheFirst',
    options: {
      cacheName: 'lazy-chunks-cache',
      expiration: {
        maxEntries: 100,
        maxAgeSeconds: 60 * 60 * 24 * 30 // 30 days
      }
    }
  },
  {
    // Map tiles, matched by their shape (src/utils/mapTiles.ts).
    // - Tile images requested without crossorigin come back opaque with
    //   status 0. That must count as cacheable, or no tile is cached.
    // - The FOSSGIS terms ask for at least seven days of caching.
    // - Stands before the generic image rule, which a tile URL with a
    //   query string would slip past.
    // - matchTileRequest is self-contained because Workbox writes a
    //   function pattern into the worker by its source text.
    urlPattern: matchTileRequest,
    handler: 'CacheFirst',
    options: {
      cacheName: 'map-tile-cache',
      cacheableResponse: {
        statuses: [0, 200]
      },
      expiration: {
        maxEntries: 500,
        maxAgeSeconds: 60 * 60 * 24 * 7 // 7 days
      }
    }
  },
  {
    urlPattern: /\.(?:png|jpg|jpeg|svg|gif|webp)$/i,
    handler: 'CacheFirst',
    options: {
      cacheName: 'image-cache',
      expiration: {
        maxEntries: 100,
        maxAgeSeconds: 60 * 60 * 24 * 30 // 30 days
      }
    }
  },
  {
    urlPattern: /\.(?:js|css|woff|woff2|ttf|otf)$/i,
    handler: 'StaleWhileRevalidate',
    options: {
      cacheName: 'static-resources',
      expiration: {
        maxEntries: 60,
        maxAgeSeconds: 60 * 60 * 24 * 7 // 7 days
      }
    }
  }
]

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    devServiceWorkerKillSwitch(),
    // Emits third-party-licenses.txt beside the production bundle.
    // MIT/BSD/ISC/Apache-2.0 notices must travel with every copy.
    thirdPartyLicensesPlugin(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['offline.html', 'icon-192.png', 'icon-512.png'],
      manifest: {
        name: 'Clavis',
        short_name: 'Clavis',
        description: 'Mobile-first city rallye platform for immigrants and newcomers',
        theme_color: '#1D47A4',
        background_color: '#FEF7FF',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        orientation: 'portrait-primary',
        icons: [
          {
            src: '/icon-192.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'any maskable'
          },
          {
            src: '/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable'
          }
        ]
      },
      workbox: {
        // A main chunk over workbox's 2-MiB default would drop out of the
        // precache, and offline support depends on it.
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
        // The staff, operator and content pages load through React.lazy.
        // Workbox's default globPatterns would precache their chunks and
        // fetch the whole app on the first visit over a weak connection.
        // So precache only what the eager participant and public app needs
        // (its non-JS assets, the entry chunk, the workbox runtime). The
        // runtimeCaching rules load the other chunks on demand.
        globPatterns: [
          '**/*.{css,html,ico,png,svg,woff2,webmanifest}',
          'assets/index-*.js',
          'assets/workbox-window*.js',
        ],
        // A precached network diagnostic would report a stale verdict, and
        // its updates would not reach users.
        globIgnores: ['netcheck.html'],
        // A hard navigation to a client-side route (/login, /dashboard) must
        // get the app shell so React Router can render it. A fallback of
        // /offline.html would answer every navigation, even online. The
        // navigation rule of runtimeCaching serves the offline page only
        // when the network is unreachable.
        navigateFallback: '/index.html',
        // The licence notices are legal text and must be served as the file.
        // The LegalLinks row links there directly, so it is a navigation
        // that navigateFallback would answer with index.html.
        navigateFallbackDenylist: [/^\/api/, /^\/third-party-licenses\.txt$/],
        runtimeCaching,
      },
      devOptions: {
        // A service worker in dev would hijack the dev server with its
        // navigateFallback. Enable it only to test offline behaviour.
        enabled: false,
        type: 'module'
      }
    })
  ],
  server: {
    host: '0.0.0.0',
    port: 3000,
    // Allow access through the Caddy ingress (localhost).
    allowedHosts: ['localhost'],
    // In compose-driven dev the browser talks only to Caddy on port 80.
    // Caddy proxies the HMR WebSocket upgrade to frontend:3000.
    hmr: {
      clientPort: 80,
      host: 'localhost',
      protocol: 'ws',
    },
    // Used only by `npm run dev` outside Docker. In compose-driven dev
    // axios sends to VITE_API_URL=http://localhost, that is through Caddy.
    proxy: {
      '/api': {
        target: 'http://api:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: true
  }
})
