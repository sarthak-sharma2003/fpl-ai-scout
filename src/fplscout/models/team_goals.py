"""Team goals model: Dixon-Coles bivariate Poisson — plan §Phase3.2.

Keyed on team `code` (persistent across seasons), not `team_id` — grounding against
real data showed `team_id` resets every season (e.g. Newcastle: 14 in 2021-22, 15 in
2025-26; `code` stays 4 in both). Fitting on `team_id` would silently treat the same
club as a different, historyless team each season.

Promoted teams (plan §6.6): a team with zero fixtures in the training window gets no
fitted attack/defense parameter from the optimizer. `predict()` falls back to the
mean of the bottom-quartile teams by fitted attack strength (a "relegation-zone
average", proxying "newly promoted teams tend to be weaker than the current top
flight") rather than the league-average default an unfit parameter would silently
imply.

Time decay: match weight = exp(-xi * days_since_match), matches closer to the fit
cutoff matter more.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

XI = 0.0018  # time-decay rate (~1 season half-life ≈ 385 days)


def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-score correlation adjustment."""
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


@dataclass
class DixonColesModel:
    attack: dict[int, float]
    defense: dict[int, float]
    home_advantage: float
    rho: float
    fallback_attack: float
    fallback_defense: float

    def _get(self, code: int) -> tuple[float, float]:
        if code in self.attack:
            return self.attack[code], self.defense[code]
        return self.fallback_attack, self.fallback_defense

    def expected_goals(self, home_code: int, away_code: int) -> tuple[float, float]:
        a_home, d_home = self._get(home_code)
        a_away, d_away = self._get(away_code)
        lam = float(np.exp(a_home + d_away + self.home_advantage))
        mu = float(np.exp(a_away + d_home))
        return lam, mu

    def clean_sheet_prob(self, home_code: int, away_code: int) -> tuple[float, float]:
        """Returns (P(home keeps CS), P(away keeps CS)) — i.e. opponent scores 0."""
        lam, mu = self.expected_goals(home_code, away_code)
        p_home_cs = float(poisson.pmf(0, mu))  # away scores 0
        p_away_cs = float(poisson.pmf(0, lam))  # home scores 0
        return p_home_cs, p_away_cs


def _neg_log_likelihood(
    params: np.ndarray,
    codes: list[int],
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
) -> float:
    n = len(codes)
    attack = params[:n]
    defense = params[n : 2 * n]
    home_adv = params[2 * n]
    rho = params[2 * n + 1]

    a_home = attack[home_idx]
    d_home = defense[home_idx]
    a_away = attack[away_idx]
    d_away = defense[away_idx]

    lam = np.exp(a_home + d_away + home_adv)
    mu = np.exp(a_away + d_home)

    ll = poisson.logpmf(home_goals, lam) + poisson.logpmf(away_goals, mu)
    tau_adj = np.array(
        [
            _tau(int(x), int(y), lam_i, mu_i, rho)
            for x, y, lam_i, mu_i in zip(home_goals, away_goals, lam, mu, strict=True)
        ]
    )
    tau_adj = np.clip(tau_adj, 1e-10, None)
    ll = ll + np.log(tau_adj)
    return -float(np.sum(ll * weights))


def refit_with_target(
    train_fixtures: pd.DataFrame,
    target_fixtures: pd.DataFrame,
    teams: pd.DataFrame,
    before_gw: int,
) -> DixonColesModel:
    """In-season refit: training fixtures plus the target season's fixtures
    strictly BEFORE `before_gw` (leak rule — a decision at gw g happens at the
    deadline, before any gw-g match kicks off). fit() itself drops rows without
    final scores, so postponed/unplayed pre-gw fixtures are excluded too. Time
    decay then weights the freshest included matches highest automatically
    (as_of defaults to the latest included kickoff)."""
    past = target_fixtures[target_fixtures["event"] < before_gw]
    return fit(pd.concat([train_fixtures, past], ignore_index=True), teams)


def fit(
    fixtures: pd.DataFrame, teams: pd.DataFrame, as_of: pd.Timestamp | None = None
) -> DixonColesModel:
    """fixtures: season/fixture_id/kickoff_time/team_h/team_a/team_h_score/team_a_score
    (finished matches only). teams: season/team_id/code, used to map team_id -> code."""
    id_to_code = {(row.season, row.team_id): row.code for row in teams.itertuples()}
    df = fixtures.dropna(subset=["team_h_score", "team_a_score"]).copy()
    df["home_code"] = [
        id_to_code.get((s, t)) for s, t in zip(df["season"], df["team_h"], strict=True)
    ]
    df["away_code"] = [
        id_to_code.get((s, t)) for s, t in zip(df["season"], df["team_a"], strict=True)
    ]
    df = df.dropna(subset=["home_code", "away_code"])

    codes = sorted(set(df["home_code"]) | set(df["away_code"]))
    code_to_idx = {c: i for i, c in enumerate(codes)}
    home_idx = df["home_code"].map(code_to_idx).to_numpy()
    away_idx = df["away_code"].map(code_to_idx).to_numpy()
    home_goals = df["team_h_score"].to_numpy(dtype=float)
    away_goals = df["team_a_score"].to_numpy(dtype=float)

    if as_of is None:
        as_of = pd.to_datetime(df["kickoff_time"]).max()
    days_ago = (as_of - pd.to_datetime(df["kickoff_time"])).dt.total_seconds() / 86400
    weights = np.exp(-XI * np.clip(days_ago, 0, None).to_numpy())

    n = len(codes)
    x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.3], [0.0]])
    result = minimize(
        _neg_log_likelihood,
        x0,
        args=(codes, home_idx, away_idx, home_goals, away_goals, weights),
        method="L-BFGS-B",
        options={"maxiter": 300},
    )
    params = result.x
    attack = dict(zip(codes, params[:n], strict=True))
    defense = dict(zip(codes, params[n : 2 * n], strict=True))
    home_adv = float(params[2 * n])
    rho = float(np.clip(params[2 * n + 1], -0.2, 0.2))

    attack_values = np.array(list(attack.values()))
    bottom_quartile_cutoff = np.quantile(attack_values, 0.25)
    bottom_codes = [c for c, a in attack.items() if a <= bottom_quartile_cutoff]
    fallback_attack = float(np.mean([attack[c] for c in bottom_codes]))
    fallback_defense = float(np.mean([defense[c] for c in bottom_codes]))

    return DixonColesModel(
        attack=attack,
        defense=defense,
        home_advantage=home_adv,
        rho=rho,
        fallback_attack=fallback_attack,
        fallback_defense=fallback_defense,
    )
