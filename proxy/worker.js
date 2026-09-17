/**
 * Ask Xabi's key-holding proxy, plus a dumb FPL API passthrough.
 *
 * Gemini route (unprefixed path): the site is static GitHub Pages, so it
 * cannot keep a secret — anything in the bundle is readable by anyone who
 * opens devtools. This Worker exists purely to be the one place a real API
 * key can live. The browser talks to this Worker, this Worker talks to
 * Gemini with the key from env, and the key never crosses the wire to a
 * visitor.
 *
 * It is a dumb passthrough on purpose. The @google/genai SDK in the browser is
 * pointed here via httpOptions.baseUrl and otherwise behaves exactly as it did
 * when it called Google directly, so there is no request/response translation
 * to keep in sync — we forward the path, swap the auth header, and stream the
 * body straight back.
 *
 * FPL route (/fpl/<path>): there's no secret here — fantasy.premierleague.com
 * just doesn't send CORS headers, so a browser can't call it directly. This
 * route forwards a small allowlist of GET paths to it and adds CORS, nothing
 * else.
 */

const UPSTREAM = 'https://generativelanguage.googleapis.com';
const FPL_UPSTREAM = 'https://fantasy.premierleague.com/api';

// Only these origins may use our key. Add a localhost entry while developing.
const ALLOWED_ORIGINS = [
  'https://sarthak-sharma2003.github.io',
  'http://localhost:5173',
];

// The FPL API has no auth to leak, but it also has no CORS headers, so the
// browser can't call it directly — that's the entire reason this route
// exists. Allowlist the exact shapes "My Team" needs; 404 everything else so
// this can't become an open proxy for the whole FPL API surface.
const FPL_PATH_PATTERNS = [
  /^entry\/\d+\/$/,
  /^entry\/\d+\/event\/\d+\/picks\/$/,
  /^bootstrap-static\/$/,
];

function corsHeaders(origin, methods, allowHeaders) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': methods,
    // The SDK sends the key header even when it is a placeholder, so it must be
    // allowed through preflight or the browser blocks the request.
    'Access-Control-Allow-Headers': allowHeaders,
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
  };
}

async function handleGemini(request, url, env, origin) {
  const headers = corsHeaders(origin, 'POST, OPTIONS', 'Content-Type, x-goog-api-key, x-goog-api-client');

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers });
  }
  if (request.method !== 'POST') {
    return new Response('Method not allowed', { status: 405, headers });
  }
  // Refuse anything that isn't a generateContent call, so a leaked endpoint
  // cannot be repurposed against the rest of the Gemini API surface.
  if (!url.pathname.startsWith('/v1beta/models/')) {
    return new Response('Not found', { status: 404, headers });
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
      ...headers,
      'Content-Type': upstream.headers.get('Content-Type') ?? 'application/json',
      'Cache-Control': 'no-store',
    },
  });
}

async function handleFpl(request, url, origin) {
  const headers = corsHeaders(origin, 'GET, OPTIONS', 'Content-Type');

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers });
  }
  if (request.method !== 'GET') {
    return new Response('Method not allowed', { status: 405, headers });
  }

  const path = url.pathname.slice('/fpl/'.length);
  if (!FPL_PATH_PATTERNS.some((pattern) => pattern.test(path))) {
    return new Response('Not found', { status: 404, headers });
  }

  const upstream = await fetch(`${FPL_UPSTREAM}/${path}${url.search}`);

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      ...headers,
      'Content-Type': upstream.headers.get('Content-Type') ?? 'application/json',
      // FPL data changes slowly enough that a 5-minute edge/browser cache is
      // invisible to users and saves a real chunk of upstream calls.
      'Cache-Control': 'public, max-age=300',
    },
  });
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

    const url = new URL(request.url);
    if (url.pathname.startsWith('/fpl/')) {
      return handleFpl(request, url, origin);
    }
    return handleGemini(request, url, env, origin);
  },
};
