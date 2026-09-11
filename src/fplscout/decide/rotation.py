"""Rotation pairs: a same-position player from another club whose fixtures
alternate with one you own, so buying him and starting whichever the model
rates higher each week beats always starting your man. The classic cheap
keeper/defender pairing, derived from the horizon forecast instead of eyeballed
off a fixture ticker.

Goalkeepers and defenders only: clean-sheet odds swing hardest with the fixture
there, and that is where the points model's ranking holds up on live data. The
midfield mean head is not trustworthy enough to schedule starts off."""

from __future__ import annotations

import numpy as np
import pandas as pd

ROTATION_POSITIONS = ("GKP", "DEF")
ROTATION_WEEKS = 6
# Each player must be the better start in at least this many weeks. Otherwise
# it is not a rotation, just one player being better than the other — and the
# Transfers page already ranks straight upgrades.
MIN_STARTS_EACH = 2


def rotation_pairs(
    ev_by_gw: pd.DataFrame,
    universe: pd.DataFrame,
    owned: set[int],
    decay: float,
    hit_cost: float,
    weeks: int = ROTATION_WEEKS,
) -> list[dict]:
    """Best rotation partner to buy for each owned GKP/DEF, best pairs first.

    ev_by_gw: code index, gameweek columns, undecayed EV (NaN = blank week).
    universe: code, position, team_id of every pickable player, owned included.

    net_gain = decayed EV from starting the better of the two each week, minus
    always starting the owned one, minus `hit_cost` for the transfer. Owned
    players are never partners: the weekly XI already starts the better of two
    men you have. Only positive net gains are returned.
    """
    gws = sorted(ev_by_gw.columns)[:weeks]
    if not gws:
        return []
    ev = ev_by_gw[gws].fillna(0.0)
    weights = decay ** np.arange(len(gws))
    info = universe.drop_duplicates("code").set_index("code")
    info = info[info.index.isin(ev.index)]
    not_owned = ~info.index.isin(list(owned))

    pairs = []
    for a in sorted(owned & set(info.index)):
        pos, club = info.at[a, "position"], info.at[a, "team_id"]
        if pos not in ROTATION_POSITIONS:
            continue
        cands = info.index[(info["position"] == pos) & (info["team_id"] != club) & not_owned]
        if not len(cands):
            continue
        ea = ev.loc[a].to_numpy()
        eb = ev.loc[cands].to_numpy()
        partner_starts = eb > ea
        n_partner = partner_starts.sum(axis=1)
        net = ((np.maximum(eb, ea) - ea) * weights).sum(axis=1) - hit_cost
        ok = (
            (net > 0)
            & (n_partner >= MIN_STARTS_EACH)
            & (len(gws) - n_partner >= MIN_STARTS_EACH)
        )
        if not ok.any():
            continue
        i = np.flatnonzero(ok)[net[ok].argmax()]
        pairs.append({
            "owned": int(a),
            "partner": int(cands[i]),
            "net_gain": round(float(net[i]), 2),
            "weeks": [
                {
                    "gw": int(g),
                    "start": "partner" if partner_starts[i, j] else "owned",
                    "owned_ev": round(float(ea[j]), 2),
                    "partner_ev": round(float(eb[i, j]), 2),
                }
                for j, g in enumerate(gws)
            ],
        })
    return sorted(pairs, key=lambda p: -p["net_gain"])
