---
version: 1
slug: "app"
primary_target: "app"
related_targets: []
---

## Direction contract

THESIS: The app reads as a UEFA matchday programme's squad-sheet pages, not a "sports app dashboard" — dense, editorial, ink-on-paper authority, refusing the glossy FUT-card / neon-gradient sports-dashboard default and its flat-fintech-SaaS opposite.

OWN-WORLD: Restrained palette — light: cream newsprint ground, near-black ink; dark: near-black matchday-night ground, warm cream ink. One accent, electric indigo (champions-night, not the trademarked starball), carrying selection/active/captain state at ~5-10% of surface. Separate semantic ramp (green/amber/red) for deadline urgency and warning states — functional, not decorative. Type: Libre Franklin (condensed cuts for labels/section heads, regular/medium for body and table data, tabular lining figures) paired with JetBrains Mono strictly for ticking digits and dense numeric columns (countdown, recoveriesPer90, value) — a functional pairing, not a "technical" costume. Hairline rules, not shadowed cards, divide sections; state (starting/bench, trusted/guessed minutes) is carried by rule weight/pattern as well as color, never color alone.

STORY: A manager opens the app before a deadline, sees the live countdown and the operational checklist first, reads the XI/captain/bench as a squad-sheet list, and leaves knowing exactly what to do — then returns mid-matchday to re-check as gamedays complete.

FIRST VIEWPORT (Now view, mobile-first): persistent top strip = countdown (monospace, urgency-ramped) + staleness note. Below: the checklist as a numbered programme-note block (numbering is real matchday-day information here, not decoration). Then squad sheet: XI as a compact list grouped by position with captain set in larger weight (billing-by-size, not a medallion icon), bench set off by a hairline rule, cost-vs-budget and projected total as a small tabular line, gameday split as a row of day chips.

FORM: Assigned direction 5 of 7 self-derived grounded candidates (matchday programme / dense editorial squad-sheet), seed key 9aee746c. Raised against catalog challengers: (1) declined bitmap-specimen challenger's discipline of integer-locked numeral scale, applied to the countdown digits; (2) competitive festival-lineup-poster challenger's pure size/weight billing hierarchy, applied to captain prominence instead of a medallion icon; (3) declined jackfield-schedule challenger's discipline of encoding state via line pattern/weight rather than hue alone, applied to trusted/guessed-minutes and starting/bench rows; (4) declined origami-crane challenger's numbered fixed-margin step column, applied to the Plan view's matchday rows.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
