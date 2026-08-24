/**
 * Ask Xabi's key-holding proxy.
 *
 * The site is static GitHub Pages, so it cannot keep a secret: anything in the
 * bundle is readable by anyone who opens devtools. This Worker exists purely to
 * be the one place a real API key can live. The browser talks to this Worker,
 * this Worker talks to Gemini with the key from env, and the key never crosses
 * the wire to a visitor.
 *
 * It is a dumb passthrough on purpose. The @google/genai SDK in the browser is
 * pointed here via httpOptions.baseUrl and otherwise behaves exactly as it did
 * when it called Google directly, so there is no request/response translation
 * to keep in sync — we forward the path, swap the auth header, and stream the
 * body straight back.
 */

const UPSTREAM = 'https://generativelanguage.googleapis.com';

// Only these origins may use our key. Add a localhost entry while developing.
const ALLOWED_ORIGINS = [
  'https://sarthak-sharma2003.github.io',
  'http://localhost:5173',
];

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    // The SDK sends the key header even when it is a placeholder, so it must be
    // allowed through preflight or the browser blocks the request.
    'Access-Control-Allow-Headers': 'Content-Type, x-goog-api-key, x-goog-api-client',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') ?? '';

    // ponytail: Origin is trivially forged by anything that isn't a browser, so
    // this stops other websites embedding our key, NOT a determined scraper.
    // If the quota starts disappearing, the upgrade is a signed token or
    // Cloudflare Turnstile — not a longer allowlist.
    if (!ALLOWED_ORIGINS.includes(origin)) {
      return new Response('Forbidden origin', { status: 403 });
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: corsHeaders(origin) });
    }

    const url = new URL(request.url);
    // Refuse anything that isn't a generateContent call, so a leaked endpoint
    // cannot be repurposed against the rest of the Gemini API surface.
    if (!url.pathname.startsWith('/v1beta/models/')) {
      return new Response('Not found', { status: 404, headers: corsHeaders(origin) });
    }

    const upstream = await fetch(`${UPSTREAM}${url.pathname}${url.search}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-goog-api-key': env.GEMINI_API_KEY,
      },
      body: request.body,
    });

    // Pass the body through undecoded so SSE chunks reach the browser as they
    // arrive; buffering here would turn streaming into a long silence.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        ...corsHeaders(origin),
        'Content-Type': upstream.headers.get('Content-Type') ?? 'application/json',
        'Cache-Control': 'no-store',
      },
    });
  },
};
