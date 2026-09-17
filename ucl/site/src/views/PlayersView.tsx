import { useMemo, useState } from "react";
import type { Player, PlayersData, Position } from "../types";
import { formatMoney } from "../lib/format";
import { SortIcon, WarningIcon } from "../components/Icons";
import { RecoveriesCell, TrustChip, TrustNote } from "../components/PlayerBits";

type SortKey = "points" | "value" | "owned" | "recoveriesPer90";
const SORT_LABEL: Record<SortKey, string> = {
  points: "Points",
  value: "Value",
  owned: "Owned",
  recoveriesPer90: "Rec/90",
};
const POSITIONS: Array<Position | "ALL"> = ["ALL", "GK", "DEF", "MID", "FWD"];

export function PlayersView({ players }: { players: PlayersData }) {
  const [pos, setPos] = useState<Position | "ALL">("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("points");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const rows = useMemo(() => {
    const filtered = pos === "ALL" ? players.players : players.players.filter((p) => p.pos === pos);
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      return (av - bv) * dir;
    });
  }, [players.players, pos, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  return (
    <div>
      <div className="now-hero">
        <div>
          <h1 className="now-hero__title">Players</h1>
          <p className="now-hero__sub">Top {players.players.length} by projection · MD{players.matchday}</p>
        </div>
      </div>

      <div className="players-toolbar">
        <div className="filter-group" role="group" aria-label="Filter by position">
          {POSITIONS.map((p) => (
            <button
              key={p}
              type="button"
              className="filter-btn"
              aria-pressed={pos === p}
              onClick={() => setPos(p)}
            >
              {p}
            </button>
          ))}
        </div>
        <span className="players-count">{rows.length} players</span>
      </div>

      <div className="table-scroll">
        <table className="players-table">
          <caption className="sr-only">
            UEFA Champions League Fantasy players, filtered by {pos}, sorted by {SORT_LABEL[sortKey]} {sortDir}ending
          </caption>
          <thead>
            <tr>
              <th scope="col" className="col-name">
                Player
              </th>
              <th scope="col">Pos</th>
              {(["value", "points", "owned", "recoveriesPer90"] as SortKey[]).map((key) => (
                <SortableHeader key={key} label={SORT_LABEL[key]} active={sortKey === key} dir={sortDir} onClick={() => toggleSort(key)} />
              ))}
              <th scope="col">Mins</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <PlayerTableRow
                key={p.id}
                p={p}
                expanded={expandedId === p.id}
                onToggle={() => setExpandedId((id) => (id === p.id ? null : p.id))}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SortableHeader({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
}) {
  return (
    <th scope="col" aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={onClick}>
        {label}
        <SortIcon dir={active ? dir : "none"} />
      </button>
    </th>
  );
}

function PlayerTableRow({ p, expanded, onToggle }: { p: Player; expanded: boolean; onToggle: () => void }) {
  const untrusted = !p.minutesTrusted;
  return (
    <>
      <tr className={untrusted ? "row--untrusted" : undefined}>
        <td className="col-name">
          <span className="player-name-cell">
            {untrusted && (
              <button
                type="button"
                className="name-warn"
                aria-expanded={expanded}
                onClick={onToggle}
                title={p.minutesNote}
                aria-label={`Guessed minutes for ${p.name} — ${p.minutesNote}`}
              >
                <WarningIcon />
              </button>
            )}
            {p.name}
            <span className="player-team">{p.team}</span>
          </span>
        </td>
        <td>{p.pos}</td>
        <td className="num">{formatMoney(p.value)}</td>
        <td className="num">{p.points.toFixed(1)}</td>
        <td className="num">{p.owned.toFixed(0)}%</td>
        <td className="num">
          <RecoveriesCell value={p.recoveriesPer90} />
        </td>
        <td>{untrusted ? <TrustChip note={p.minutesNote} onToggle={onToggle} open={expanded} /> : `${p.minutes}'`}</td>
      </tr>
      {expanded && untrusted && (
        <tr className="note-row">
          <td colSpan={7}>
            <TrustNote note={p.minutesNote} />
          </td>
        </tr>
      )}
    </>
  );
}
