"""Live-data health checks — plan §Phase3 review follow-up.

`fpl_xp`/`ep_next` carries 63-65% of total gain in the points model's FULL variant
(see models/points.py). Production always has this feature available from
bootstrap-static, but if FPL's own `ep_next` ever goes degenerate mid-season (all
zero, all null, or otherwise nonsensical), that's silently the single biggest risk
to model quality — worse than a scraping outage, because nothing else fails loudly.
Checked healthy live 2026-07-08: per-position means ranged 0.68-1.31 (element_type
1=GKP .. 4=FWD); vaastav's own historical xP outages (see models/dataset.py) showed
entire-gameweek means going to exactly 0.0, so that's the specific failure shape to
watch for.
"""

from __future__ import annotations

from fplscout.ingest.schemas import BootstrapStatic

DEGENERATE_MEAN_THRESHOLD = 0.2  # healthy range observed live: ~0.6-1.3 per position
HIGH_NULL_FRACTION_THRESHOLD = 0.5


def check_ep_next_health(bootstrap: BootstrapStatic) -> list[str]:
    """Returns a list of human-readable warnings; empty means healthy."""
    warnings: list[str] = []

    by_position: dict[int, list[float]] = {}
    null_count = 0
    for element in bootstrap.elements:
        if element.ep_next is None:
            null_count += 1
            continue
        try:
            value = float(element.ep_next)
        except ValueError:
            continue
        by_position.setdefault(element.element_type, []).append(value)

    for position_id in sorted(by_position):
        values = by_position[position_id]
        mean = sum(values) / len(values)
        if mean < DEGENERATE_MEAN_THRESHOLD:
            warnings.append(
                f"element_type {position_id}: mean ep_next={mean:.3f} across "
                f"{len(values)} players looks degenerate (healthy range observed "
                f"live is roughly 0.6-1.3). This is the failure shape seen in "
                f"vaastav's historical xP outages (see models/dataset.py)."
            )

    total = len(bootstrap.elements)
    if total > 0 and null_count / total > HIGH_NULL_FRACTION_THRESHOLD:
        warnings.append(
            f"{null_count}/{total} players have null ep_next "
            f"({null_count / total:.0%}) — well above normal."
        )

    return warnings
