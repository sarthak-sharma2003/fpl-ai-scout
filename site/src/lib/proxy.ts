// Deployed proxy/worker.js — the Cloudflare Worker backing My Team's FPL
// import (/fpl/<path> passthrough, since fantasy.premierleague.com sends no
// CORS headers of its own). Ask Xabi does NOT route through it: it stays
// BYO-key in the browser (the 08-23 funding decision), so the Worker holds
// no Gemini secret. Deployed 2026-09-18 from proxy/ via `wrangler deploy`.
export const PROXY_URL = 'https://xabi-proxy.xabis-longxo.workers.dev';
