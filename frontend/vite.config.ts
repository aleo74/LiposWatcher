import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [react(), VitePWA({
    registerType: 'autoUpdate',
    manifest: { name: 'LipoWatcher', short_name: 'LipoWatcher', description: 'Carnet de suivi des batteries RC', lang: 'fr', start_url: '/', display: 'standalone', background_color: '#f7f8f5', theme_color: '#182f2d', icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' }
    ] },
    workbox: { navigateFallback: '/index.html', runtimeCaching: [{ urlPattern: /\/api\//, handler: 'NetworkOnly' }] }
  })],
  server: { proxy: { '/api': 'http://localhost:8000' } }
})
