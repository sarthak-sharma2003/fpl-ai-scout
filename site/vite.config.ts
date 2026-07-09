import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base must match the GitHub Pages project-site path (github.io/<repo>/) or
// every asset/data fetch 404s once deployed — see .github/workflows.
export default defineConfig({
  base: '/fpl-ai-scout/',
  plugins: [react(), tailwindcss()],
})
