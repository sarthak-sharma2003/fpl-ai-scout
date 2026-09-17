import { useMemo } from "react";
import type { TeamsData } from "../types";

export function TeamsView({ teams }: { teams: TeamsData }) {
  const sorted = useMemo(() => [...teams.teams].sort((a, b) => b.elo - a.elo), [teams.teams]);

  return (
    <div>
      <div className="now-hero">
        <div>
          <h1 className="now-hero__title">Teams</h1>
          <p className="now-hero__sub">{teams.teams.length} clubs, ranked by Elo</p>
        </div>
      </div>

      <div className="table-scroll">
        <table className="teams-table">
          <caption className="sr-only">Champions League clubs ranked by Elo rating</caption>
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Club</th>
              <th scope="col">Pot</th>
              <th scope="col">Elo</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t, i) => (
              <tr key={t.id} className={t.eliminated ? "row--eliminated" : undefined}>
                <td className="num">{i + 1}</td>
                <td>
                  <span className="team-name">{t.name}</span>
                </td>
                <td>
                  <span className="pot-badge" title={`Seeding pot ${t.pot}`}>
                    {t.pot}
                  </span>
                </td>
                <td className="num">{t.elo}</td>
                <td>{t.eliminated && <span className="eliminated-tag">Eliminated</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
