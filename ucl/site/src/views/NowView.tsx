import { useId, useState } from "react";
import type { SquadData, SquadPick } from "../types";
import { formatDay, formatMoney } from "../lib/format";
import { useCountdown } from "../lib/useCountdown";
import { WarningIcon } from "../components/Icons";
import { CountdownHero } from "../components/CountdownHero";
import { TrustNote } from "../components/PlayerBits";
import { kitFor } from "../lib/clubColours";

const POS_ORDER = ["GK", "DEF", "MID", "FWD"] as const;
const POS_LABEL: Record<string, string> = { GK: "Goalkeeper", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" };

export function NowView({ squad }: { squad: SquadData }) {
  const s = squad.squad;
  const starters = s.picks.filter((p) => p.starting);
  const bench = s.picks.filter((p) => !p.starting).sort((a, b) => b.points - a.points);
  const captain = s.picks.find((p) => p.captain);
  const over = s.cost > squad.budget;
  const expired = useCountdown(squad.deadline).urgency === "expired";

  return (
    <div>
      <header>
        <h1 className="display-title">Matchday {squad.matchday}</h1>
        <CountdownHero deadlineIso={squad.deadline} matchday={squad.matchday} />
      </header>

      {expired && (
        <div className="deadline-banner deadline-banner--expired" role="alert">
          <WarningIcon />
          <div>
            <div className="deadline-banner__title">This is the locked squad</div>
            <div className="deadline-banner__detail">
              UCL Fantasy has locked MD{squad.matchday} in. Check the gameday split below for which days are still
              to play.
            </div>
          </div>
        </div>
      )}

      <div className="summary-row">
        <div className="summary-cell">
          <p className="summary-cell__label">Cost</p>
          <p className={over ? "summary-cell__value summary-cell__value--over" : "summary-cell__value"}>
            {formatMoney(s.cost)}
          </p>
        </div>
        <div className="summary-cell">
          <p className="summary-cell__label">Budget</p>
          <p className="summary-cell__value">{formatMoney(squad.budget)}</p>
        </div>
        <div className="summary-cell">
          <p className="summary-cell__label">XI projected</p>
          <p className="summary-cell__value">{s.xiPoints.toFixed(1)}</p>
        </div>
        <div className="summary-cell">
          <p className="summary-cell__label">Captain</p>
          <p className="summary-cell__value">{captain?.name ?? "—"}</p>
        </div>
      </div>

      <h2 className="section-label">Gameday split</h2>
      <div className="gameday-row">
        {squad.gamedays.map((day) => {
          const today = day === new Date().toISOString().slice(0, 10);
          return (
            <div key={day} className={today ? "gameday-chip gameday-chip--today" : "gameday-chip"}>
              <span className="gameday-chip__day">{formatDay(day)}</span>
              <span className="gameday-chip__count">{s.byDay[day] ?? 0} playing</span>
            </div>
          );
        })}
      </div>

      <h2 className="section-label">Starting XI</h2>
      <div className="pitch">
        <PitchMarkings />
        {POS_ORDER.map((pos) => {
          const inPos = starters.filter((p) => p.pos === pos);
          if (inPos.length === 0) return null;
          return <PitchRow key={pos} label={POS_LABEL[pos]} players={inPos} />;
        })}
      </div>

      <h2 className="section-label bench-label">Bench</h2>
      <div className="squad-group">
        {bench.map((p) => (
          <PlayerRow key={p.id} p={p} bench />
        ))}
      </div>

      <h2 className="section-label">Checklist</h2>
      <ol className="checklist">
        {squad.checklist.map((item, i) => {
          const warn = item.startsWith("WARNING:");
          const text = warn ? item.slice("WARNING: ".length) : item;
          return (
            <li key={i} className={warn ? "checklist__item checklist__item--warning" : "checklist__item"}>
              <span className="checklist__num">{i + 1}</span>
              <span className="checklist__text">{warn ? text[0].toUpperCase() + text.slice(1) : text}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Purely decorative pitch markings — mown-stripe turf comes from CSS on
 * .pitch itself; these are the chalk lines (box, six-yard, D, spot, and the
 * centre circle poking up from the bottom edge). aria-hidden: the real
 * structure for assistive tech is the position-grouped lists below. */
function PitchMarkings() {
  return (
    <div className="pitch-lines" aria-hidden="true">
      <div className="pitch-line pitch-line--outer" />
      <div className="pitch-line pitch-line--penalty" />
      <div className="pitch-line pitch-line--six" />
      <div className="pitch-line pitch-line--d" />
      <div className="pitch-line pitch-line--spot" />
      <div className="pitch-line pitch-line--circle" />
    </div>
  );
}

function PitchRow({ label, players }: { label: string; players: SquadPick[] }) {
  const id = useId();
  return (
    <div>
      <p className="pitch-legend" id={id}>
        {label}
      </p>
      <ul className="pitch-row" aria-labelledby={id}>
        {players.map((p) => (
          <PitchCard key={p.id} p={p} />
        ))}
      </ul>
    </div>
  );
}

/** The pitch card shows only what a formation graphic shows: kit, name, and
 * club/price/position. Points, the recoveries edge, and the guessed-minutes
 * warning all stay on the bench rows and the Players table — adding them
 * here is what made the formation read as cluttered.
 *
 * Kit, not position, carries the stripe now: two players from the same club
 * render identically, so the pitch reads as eleven shirts, not a bag of
 * position-coloured tiles. Position survives as the quiet letter at the end
 * of the club/price line. */
function PitchCard({ p }: { p: SquadPick }) {
  const kit = kitFor(p.teamId);
  return (
    <li className="pitch-card">
      {p.captain && (
        <span className="armband-roundel" title="Captain" aria-label="Captain">
          C
        </span>
      )}
      <div
        className="pitch-card__inner"
        style={{ backgroundImage: `linear-gradient(180deg, ${kit.primary}2e 0%, transparent 60%)` }}
      >
        <span
          className="pitch-card__stripe"
          style={{ background: `linear-gradient(90deg, ${kit.primary} 50%, ${kit.secondary} 50%)` }}
        />
        <div className="pitch-card__body">
          <p className="pitch-card__name">{p.name}</p>
          <p className="pitch-card__team">
            {p.team} · {formatMoney(p.value)} · {p.pos[0]}
          </p>
        </div>
      </div>
    </li>
  );
}

function PlayerRow({ p, bench }: { p: SquadPick; bench?: boolean }) {
  const [showNote, setShowNote] = useState(false);
  const classes = ["player-row"];
  if (p.captain) classes.push("player-row--captain");
  if (bench) classes.push("player-row--bench");
  if (!p.minutesTrusted) classes.push("player-row--untrusted");

  return (
    <div>
      <div className={classes.join(" ")}>
        <div className="player-row__name-group">
          <span className="player-row__name">
            {p.captain && (
              <span className="captain-mark" title="Captain">
                C
              </span>
            )}
            {p.name}
          </span>
          <span className="player-row__team">
            {p.team} · {formatDay(p.day)}
          </span>
        </div>
        {!p.minutesTrusted && (
          <button
            type="button"
            className="trust-chip"
            aria-expanded={showNote}
            onClick={() => setShowNote((o) => !o)}
            title={p.minutesNote}
          >
            <WarningIcon />
            Guessed mins
          </button>
        )}
        {p.recoveriesPer90 != null && p.recoveriesPer90 >= 6 && (
          <span className="badge badge--recoveries" title={`${p.recoveriesPer90.toFixed(2)} recoveries/90`}>
            {p.recoveriesPer90.toFixed(1)}
            <span className="badge__unit"> rec/90</span>
          </span>
        )}
        <span className="player-row__stats">
          <span className="player-row__value">{formatMoney(p.value)}</span>
          <span className="player-row__points">{p.points.toFixed(1)}</span>
        </span>
      </div>
      {showNote && !p.minutesTrusted && (
        <div style={{ padding: "0 0 0.6rem" }}>
          <TrustNote note={p.minutesNote} />
        </div>
      )}
    </div>
  );
}
