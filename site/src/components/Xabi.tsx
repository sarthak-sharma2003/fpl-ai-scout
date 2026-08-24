import { useEffect, useRef, useState } from 'react';
import { GoogleGenAI, ThinkingLevel } from '@google/genai';
import { useJson } from '../lib/useJson';
import type { Dashboard, PlayerCard, Projections, Rule, Transfers } from '../types';

/** Ask-the-manager chatbot: explains and defends the squad the optimizer picked.
 *
 * Runs ENTIRELY IN THE BROWSER against the user's own API key, because this site
 * is a static GitHub Pages deploy with no backend (see the README's static-site
 * pivot) — there is nowhere to keep a server-side secret. The key lives in this
 * browser's localStorage and is sent only to Google's API; it never reaches
 * the repo, the build, or another visitor. The alternative — a serverless proxy
 * holding one shared key — is the right call if this ever needs to answer for
 * people who don't have their own key, and is the only reason to add a backend.
 *
 * Gemini because it has a real free tier. The brief below is ~9k tokens and is
 * re-sent on every turn — that is the shape that blows through the tokens-per-
 * minute cap on the other free tiers.
 *
 * Context is the already-published JSON (nothing new to generate): the squad,
 * the transfer plan, the decision rules the optimizer actually ran under, and a
 * one-line row for all ~570 projected players.
 */

// Free-tier eligible. If the preview is ever retired, 'gemini-2.5-flash' is the
// stable fallback — also free-tier, but takes thinkingBudget: 0 instead of a
// thinkingLevel.
const MODEL = 'gemini-3-flash-preview';
// Deliberately not the old 'xabi_api_key': a leftover sk-ant- key would fail
// here with an auth error nobody could explain.
const KEY_STORAGE = 'xabi_gemini_key';
// Deployed proxy/worker.js, which holds the shared key so visitors don't need
// one. Empty string = not deployed yet, in which case the key gate is the only
// way in and the widget behaves exactly as it did before the proxy existed.
const PROXY_URL = '';

type Turn = { role: 'user' | 'assistant'; text: string };

function line(p: PlayerCard, role?: string) {
  const ev = p.ev == null ? '?' : p.ev.toFixed(2);
  const flag = p.flag?.status && p.flag.status !== 'a' ? ` FLAGGED:${p.flag.status}` : '';
  return `${p.name} (${p.position} ${p.team} £${p.price ?? '?'}m ev=${ev}${flag})${role ? ` [${role}]` : ''}`;
}

/** The whole brief, rebuilt only when the published data changes. */
function buildSystem(
  dash: Dashboard,
  proj: Projections,
  transfers: Transfers,
  rules: Rule[],
): string {
  const xi = [...dash.pitch.gk, ...dash.pitch.def, ...dash.pitch.mid, ...dash.pitch.fwd];
  const role = (p: PlayerCard) =>
    p.code === dash.captain_code ? 'CAPTAIN' : p.code === dash.vice_captain_code ? 'VICE' : undefined;

  const moves = [...transfers.moves, ...transfers.alternatives]
    .map(
      (m, i) =>
        `${i < transfers.moves.length ? 'RECOMMENDED' : 'considered'}: OUT ${m.out.name} (ev ${m.compare.out_ev ?? '?'}) -> IN ${m.in.name} (ev ${m.compare.in_ev ?? '?'}), net ${m.net_ev.toFixed(2)}`,
    )
    .join('\n');

  // Club counts, computed rather than left to the model. Asked "can we swap
  // Anderson for Cherki?" it counted three City players, decided the max-3 rule
  // blocked it, and defended that twice — but a same-club one-out-one-in is
  // count-neutral, so the swap was always legal. Handing it the counts and the
  // binding condition removes the arithmetic that produced the wrong answer.
  const byClub = new Map<string, string[]>();
  for (const p of [...xi, ...dash.bench_order]) {
    // team is nullable in the published schema; a player with no club can't
    // count against a club limit, so drop them rather than bucket them under a
    // fake key that would make some other club look full.
    if (!p.team) continue;
    byClub.set(p.team, [...(byClub.get(p.team) ?? []), p.name]);
  }
  const clubCounts = [...byClub.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([team, names]) => `${team} ${names.length}/3 (${names.join(', ')})`)
    .join('\n');

  // Every projected player, one line, best EV first — so a question about a
  // player who ISN'T in the squad (the common case: "why no Gabriel?") can be
  // answered with his real number instead of a guess.
  const pool = [...proj.players]
    .sort((a, b) => (b.ev_points ?? 0) - (a.ev_points ?? 0))
    .map(
      (p) =>
        `${p.name}|${p.position}|${p.team}|£${p.price}m|ev ${p.ev_points?.toFixed(2)}|mins ${p.ev_minutes?.toFixed(0)}${p.pk ? '|PK' : ''}`,
    )
    .join('\n');

  return `You are Xabi, the manager behind "Xabi's Long-Xo" — a Fantasy Premier League squad picked by a model, not by vibes. You are talking to the human who owns this team. Your job is to explain the decisions, defend them with the actual numbers, and concede honestly when the human has a point.

GAMEWEEK ${dash.gw}, season ${dash.season}. Data state: ${dash.state}.
Bank £${transfers.bank ?? '?'}m. Free transfers: ${transfers.free_transfers ?? '?'}. Plan confidence: ${transfers.confidence}.

STARTING XI
${xi.map((p) => line(p, role(p))).join('\n')}

BENCH (in autosub order)
${dash.bench_order.map((p) => line(p)).join('\n')}

TRANSFER PLAN
${moves || 'No transfer recommended this week.'}
${transfers.chip_advice ? `Chip advice: ${transfers.chip_advice.chip} around GW${transfers.chip_advice.gw}.` : ''}

CLUB COUNTS (max 3 per club). A transfer breaks this rule ONLY when the player coming IN plays for a club already at 3/3 AND the player going OUT plays for a different club. Swapping one player for another AT THE SAME CLUB never changes a count and is always legal, however full that club looks here. Do not compute these yourself — read them:
${clubCounts}

THE RULES THIS SQUAD WAS PICKED UNDER — these are the real constraints in the optimizer. Quote them by name only after checking the rule actually binds on the specific move being asked about; a rule that does not apply is not a reason:
${rules.map((r) => `- ${r.title}: ${r.body}`).join('\n')}

EVERY PROJECTED PLAYER (name|pos|team|price|expected points this GW|expected minutes|PK = penalty taker), best first:
${pool}

HOW TO ANSWER
- Lead with the actual number. "Gabriel projects 4.1 and Tarkowski 5.7 at £1.50m less" beats "Gabriel wasn't quite good enough."
- ev is expected points for THIS gameweek. The squad itself is chosen on an 8-gameweek decayed horizon, so a player can be benched despite a good single-week number — say so when that's the reason.
- You did not pick this squad by hand. A mixed-integer solver did, under a £100m budget, max 3 per club, and valid-formation constraints. That means a player is often out not because he is bad but because the money or the club slot was needed elsewhere. Check the numbers before assuming it was a quality call.
- YOU CANNOT RE-RUN THE SOLVER. You have projections, not an optimizer. If asked to change the squad, name the specific swap and quote the EV cost of it ("forcing Gabriel in for Tarkowski costs 1.6 ev and £1.5m"), then say plainly that a real re-optimisation has to come from \`fplscout optimize\`. Never invent a re-optimised XI and present it as the model's.
- Never refuse a move as rule-breaking without naming the rule AND the number that makes it bind ("Chelsea are already 3/3 and Anderson is City, so that one is out"). If you cannot produce that number, the move is legal — argue it on EV instead, or agree.
- "In short" means one or two sentences. Obey it literally; do not answer a request for brevity with three paragraphs.
- If the human is right — a flag you can see in the data, a rule that genuinely cuts the other way, a number that doesn't support the pick — say so directly and say what would change the call.
- Talk like a manager in a press conference: direct, specific, no hedging, no bullet-point lectures. Two or three short paragraphs at most. No preamble.`;
}

function ApiKeyGate({ onSave }: { onSave: (key: string) => void }) {
  const [value, setValue] = useState('');
  return (
    <form
      className="p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSave(value.trim());
      }}
    >
      <p className="mb-3 text-[13px] leading-relaxed text-ink-500">
        Xabi answers from your own Gemini API key — free from{' '}
        <a
          href="https://aistudio.google.com/apikey"
          target="_blank"
          rel="noreferrer"
          className="text-volt underline"
        >
          Google AI Studio
        </a>
        . It is stored in this browser only and sent straight to Google — never to this
        site's server, because there isn't one.
      </p>
      <input
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Paste your Gemini API key"
        className="w-full rounded-sm border border-line bg-pitch-950 px-3 py-2 font-mono text-[12px] text-ink-100 outline-none focus:border-volt"
      />
      <button
        type="submit"
        className="mt-3 w-full rounded-sm bg-volt px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-pitch-950 disabled:opacity-40"
        disabled={!value.trim()}
      >
        Save key
      </button>
    </form>
  );
}

export default function Xabi() {
  const [open, setOpen] = useState(false);
  const [apiKey, setApiKey] = useState<string | null>(() => localStorage.getItem(KEY_STORAGE));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const dash = useJson<Dashboard>('dashboard.json');
  const proj = useJson<Projections>('projections.json');
  const transfers = useJson<Transfers>('transfers.json');
  const rules = useJson<Rule[]>('rules.json');
  const ready =
    dash.status === 'ready' &&
    proj.status === 'ready' &&
    transfers.status === 'ready' &&
    rules.status === 'ready';

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, busy]);

  async function ask(question: string) {
    if (!ready || (!apiKey && !PROXY_URL)) return;
    const system = buildSystem(dash.data, proj.data, transfers.data, rules.data);
    const history: Turn[] = [...turns, { role: 'user', text: question }];
    setTurns([...history, { role: 'assistant', text: '' }]);
    setDraft('');
    setBusy(true);
    setError(null);
    try {
      // A visitor's own key wins over the shared one: they asked for it, and
      // it keeps their questions off the proxy's quota. Without a key we point
      // the same SDK at the Worker, which swaps in the real key server-side —
      // so there is no second request path to keep working.
      const ai = apiKey
        ? new GoogleGenAI({ apiKey })
        : new GoogleGenAI({ apiKey: 'unused-proxy-holds-the-real-one', httpOptions: { baseUrl: PROXY_URL } });
      const stream = await ai.models.generateContentStream({
        model: MODEL,
        contents: history.map((t) => ({
          role: t.role === 'assistant' ? 'model' : 'user',
          parts: [{ text: t.text }],
        })),
        config: {
          systemInstruction: system,
          maxOutputTokens: 8000,
          // LOW, not MINIMAL. This looked like pure lookup-and-argue, but
          // questions like "swap Anderson for Cherki?" are small feasibility
          // checks, and MINIMAL skipped them into a confident wrong answer.
          // Thinking tokens come out of maxOutputTokens, hence the raised
          // budget — spend it here rather than risk an empty response.
          thinkingConfig: { thinkingLevel: ThinkingLevel.LOW },
        },
      });
      for await (const chunk of stream) {
        const delta = chunk.text;
        if (!delta) continue;
        setTurns((prev) =>
          prev.map((t, i) => (i === prev.length - 1 ? { ...t, text: t.text + delta } : t)),
        );
      }
    } catch (err) {
      // Drop the empty assistant bubble so a failed turn doesn't poison the
      // history sent on the next question.
      setTurns(history);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-30 rounded-full bg-volt px-5 py-3 font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-pitch-950 shadow-lg"
      >
        Ask Xabi
      </button>
    );
  }

  return (
    <div className="fixed bottom-5 right-5 z-30 flex h-[min(560px,80vh)] w-[min(420px,calc(100vw-2.5rem))] flex-col rounded-lg border border-line bg-pitch-850 shadow-2xl">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="flex items-center gap-2 font-mono text-[11px] font-bold uppercase tracking-[0.22em] text-ink-300">
          <span aria-hidden className="h-2 w-2 shrink-0 bg-volt" />
          Ask Xabi
        </h2>
        <button
          onClick={() => setOpen(false)}
          aria-label="Close"
          className="font-mono text-[16px] leading-none text-ink-500 hover:text-ink-100"
        >
          ×
        </button>
      </div>

      {!apiKey && !PROXY_URL ? (
        <ApiKeyGate
          onSave={(k) => {
            localStorage.setItem(KEY_STORAGE, k);
            setApiKey(k);
          }}
        />
      ) : (
        <>
          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
            {turns.length === 0 && (
              <p className="text-[13px] leading-relaxed text-ink-500">
                Ask why someone is in the squad, why someone isn't, or push back on the
                transfer. He has every projection and every rule the optimizer ran under.
              </p>
            )}
            {turns.map((t, i) => (
              <div
                key={i}
                className={
                  t.role === 'user'
                    ? 'ml-8 rounded-sm bg-white/5 px-3 py-2 text-[13px] text-ink-100'
                    : 'whitespace-pre-wrap text-[13px] leading-relaxed text-ink-300'
                }
              >
                {t.text || (busy ? '…' : '')}
              </div>
            ))}
            {error && <p className="text-[12px] text-fwd">{error}</p>}
          </div>

          <form
            className="flex gap-2 border-t border-line p-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim() && !busy) void ask(draft.trim());
            }}
          >
            {/* Placeholder deliberately names no player: the squad changes every
                week, and "why no Gabriel?" eventually asks about someone who is
                in the XI. */}
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={ready ? 'Why did you pick this XI?' : 'Loading data…'}
              disabled={!ready || busy}
              className="min-w-0 flex-1 rounded-sm border border-line bg-pitch-950 px-3 py-2 text-[13px] text-ink-100 outline-none focus:border-volt disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!ready || busy || !draft.trim()}
              className="rounded-sm bg-volt px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-pitch-950 disabled:opacity-40"
            >
              {busy ? '…' : 'Ask'}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
