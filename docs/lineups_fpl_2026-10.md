# FPL predicted line-ups: do they add start information beyond the minutes model?

Analysis 2026-10-13 (delegated, leak-free: each line-up snapshot = last git commit of
pabloroldan98/knapsack-football-formations strictly before the FPL deadline; model
predictions out-of-sample). Scripts were kept in a scratch area, not in this repo.

**Answer: yes — RotoWire's predicted XI.** On gameweeks the overlay was not fit on
(2025-26 GW22-38), adding RotoWire to our P(60+) cut start log-loss 0.2250 → 0.2069
(Δ −0.018 [−0.022, −0.014]) and Brier 0.0705 → 0.0631; better in 22/22 GWs. On
2026-27 GW1-5: log-loss −16%, Brier −22%. Most of the gain is the XI / not-XI flag.

| RotoWire level | start rate 25-26 | 60+ rate |
|---|---|---|
| 0.8 predicted XI | 85.5% | 80.9% |
| 0.5 XI, questionable | 64.9% | 62.3% |
| 0.2 injury list | 15.1% | 13.9% |
| 0.0 out / suspended | 0.2% | 0.2% |
| not listed | 6.3% | 5.4% |

Rotation surprise: our P60 ≥ 0.7 but RotoWire not listing him (~8 players/GW) →
started 51%, scored 2.18 vs 3.49 points (−1.31 [−1.63, −0.94]); −1.09 after
controlling for our P60 band. Reverse case (our P60 < 0.3, RotoWire XI): started 59.5%.

Recommended overlay (fit on all 29,707 matched rows; lg = logit):

    adj_P60 = sigmoid(-0.989 + 0.733*lg(P60) + c60[L]),  c60 = {0.8:+1.660, 0.5:+1.323, 0.2:+0.086, 0.0:-1.853, NL:0}
    adj_P1+ = sigmoid(-0.299 + 0.872*lg(1-P0) + c1[L]),  c1  = {0.8:+1.188, 0.5:+0.410, 0.2:+0.097, 0.0:-1.148, NL:0}
    P60' = adj_P60; P1-59' = max(adj_P1+ - adj_P60, 0); P0' = 1 - P60' - P1-59'

Guards: snapshot < 48h old and after the team's previous PL kickoff + 2h15, no 1.0
("confirmed") values; apply the NL coefficient only when the team's matched XI has
exactly 11 players; SportsGambler XI flag only as a fallback
(60+: −0.828 + 0.793·lg(P60) + 1.354·XI). Caveats: ~1 season of history; the 0.5/0.2
levels rest on small samples; not yet a training feature (no history before 2025-26).
