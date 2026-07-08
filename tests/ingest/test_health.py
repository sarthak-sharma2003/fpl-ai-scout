from __future__ import annotations

from fplscout.ingest.health import check_ep_next_health
from fplscout.ingest.schemas import BootstrapStatic


def _bootstrap(load_fixture, ep_next_overrides: dict[int, str | None]) -> BootstrapStatic:
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        if element["id"] in ep_next_overrides:
            element["ep_next"] = ep_next_overrides[element["id"]]
    return BootstrapStatic.model_validate(data)


def test_healthy_ep_next_has_no_warnings(load_fixture):
    data = load_fixture("bootstrap_static.json")
    # give every sampled element a healthy-looking value
    for element in data["elements"]:
        element["ep_next"] = "1.0"
    bootstrap = BootstrapStatic.model_validate(data)
    assert check_ep_next_health(bootstrap) == []


def test_degenerate_mean_triggers_warning(load_fixture):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = "0.0"
    bootstrap = BootstrapStatic.model_validate(data)
    warnings = check_ep_next_health(bootstrap)
    assert len(warnings) > 0
    assert any("degenerate" in w for w in warnings)


def test_high_null_fraction_triggers_warning(load_fixture):
    data = load_fixture("bootstrap_static.json")
    for element in data["elements"]:
        element["ep_next"] = None
    bootstrap = BootstrapStatic.model_validate(data)
    warnings = check_ep_next_health(bootstrap)
    assert any("null ep_next" in w for w in warnings)
