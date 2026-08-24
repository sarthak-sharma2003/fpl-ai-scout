# Ask Xabi proxy

Holds the Gemini API key so visitors don't need their own.

The site is static GitHub Pages, so it has nowhere to keep a secret — anything
in the bundle is readable by anyone. This Worker is the one place a real key can
live. The browser's `@google/genai` SDK is pointed at this Worker via
`httpOptions.baseUrl`; the Worker swaps a placeholder auth header for the real
key and streams the response straight back, so nothing about the request shape
changes and there is no second code path in the frontend.

## Deploy

You have to run these — they need a Cloudflare login and they handle the key.

```sh
cd proxy
npx wrangler login            # opens a browser, authorises your account
npx wrangler secret put GEMINI_API_KEY   # paste the key at the prompt
npx wrangler deploy
```

`deploy` prints a URL like `https://xabi-proxy.<subdomain>.workers.dev`.
Put it in `PROXY_URL` in `site/src/components/Xabi.tsx`, then push — the deploy
workflow rebuilds the site and the key gate disappears for visitors.

`wrangler secret put` stores the key encrypted in Cloudflare. It is never
written to `wrangler.toml` or any other file in this repo. Don't add it to
`wrangler.toml` as a `[vars]` entry — those are committed in plaintext.

## What protects the key

`ALLOWED_ORIGINS` in `worker.js` rejects requests that don't come from the site.
That stops another website embedding your quota. It does **not** stop someone
calling the Worker directly with `curl` and a forged `Origin` header — browsers
enforce Origin, everything else can lie about it.

That is a deliberate trade for now: the exposure is your free-tier quota, not
the key itself, which never leaves Cloudflare. If quota starts vanishing, the
fix is Cloudflare Turnstile or a signed token from the site, not more origins.

There is no rate limit in the Worker, so one visitor asking a lot can exhaust
the daily free-tier quota for everyone. Cloudflare's Rate Limiting rules can cap
requests per IP without any code change, and that is the first thing to reach
for.

## Local development

```sh
echo 'GEMINI_API_KEY=your-key-here' > .dev.vars   # gitignored
npx wrangler dev
```

Then set `PROXY_URL` to `http://localhost:8787` while developing.
`http://localhost:5173` is already in `ALLOWED_ORIGINS`.
