"""Typed client for the (unofficial, no-auth) FPL API.

Politeness rules from plan §0: cache aggressively, browser User-Agent, throttle to
<=1 req/sec, retry with backoff, never depend on the authenticated `my-team` endpoint.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fplscout.ingest.schemas import (
    BootstrapStatic,
    ElementSummary,
    Entry,
    EntryHistory,
    EntryPicks,
    EventLive,
    Fixture,
    LeagueStandings,
    Transfer,
)

BASE_URL = "https://fantasy.premierleague.com/api"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 fpl-ai-scout/0.1"
)

# Default cache TTLs, in seconds. Overridable per call.
TTL_BOOTSTRAP = 6 * 3600
TTL_FIXTURES = 6 * 3600
TTL_ELEMENT_SUMMARY = 6 * 3600
TTL_EVENT_LIVE = 5 * 60
TTL_ENTRY = 3600


class SchemaDriftError(RuntimeError):
    """Raised when an FPL API payload no longer matches our pydantic models.

    Deliberately not caught anywhere — a season-reset schema change must break the
    pipeline loudly, per plan §0/§10, not degrade silently.
    """


class _Throttle:
    """Process-wide minimum interval between requests."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            remaining = self.min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.monotonic()


@dataclass
class FplApiClient:
    cache_dir: Path
    min_interval: float = 1.0
    timeout: float = 15.0
    _http: httpx.Client = field(init=False, repr=False)
    _throttle: _Throttle = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=self.timeout,
        )
        self._throttle = _Throttle(self.min_interval)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FplApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level fetch with cache + throttle + retry -----------------------

    def _cache_path(self, cache_key: str) -> Path:
        safe_key = cache_key.replace("/", "_")
        return self.cache_dir / f"{safe_key}.json"

    def _read_cache(self, cache_key: str, ttl_seconds: float) -> dict | None:
        path = self._cache_path(cache_key)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        fetched_at = datetime.fromisoformat(envelope["fetched_at"])
        age = (datetime.now(UTC) - fetched_at).total_seconds()
        if age > ttl_seconds:
            return None
        return envelope["data"]

    def _write_cache(self, cache_key: str, data: Any) -> None:
        path = self._cache_path(cache_key)
        envelope = {"fetched_at": datetime.now(UTC).isoformat(), "data": data}
        path.write_text(json.dumps(envelope))

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _request(self, path: str, params: dict | None = None) -> Any:
        self._throttle.wait()
        response = self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _get(
        self,
        path: str,
        *,
        cache_key: str,
        ttl_seconds: float,
        params: dict | None = None,
        force_refresh: bool = False,
    ) -> Any:
        if not force_refresh:
            cached = self._read_cache(cache_key, ttl_seconds)
            if cached is not None:
                return cached
        data = self._request(path, params=params)
        self._write_cache(cache_key, data)
        return data

    # -- typed endpoints -------------------------------------------------

    def bootstrap_static(
        self, ttl_seconds: float = TTL_BOOTSTRAP, force_refresh: bool = False
    ) -> BootstrapStatic:
        raw = self._get(
            "/bootstrap-static/",
            cache_key="bootstrap_static",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return self._parse(BootstrapStatic, raw, context="bootstrap-static")

    def fixtures(
        self,
        event: int | None = None,
        ttl_seconds: float = TTL_FIXTURES,
        force_refresh: bool = False,
    ) -> list[Fixture]:
        params = {"event": event} if event is not None else None
        cache_key = f"fixtures_event_{event}" if event is not None else "fixtures_all"
        raw = self._get(
            "/fixtures/",
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            params=params,
            force_refresh=force_refresh,
        )
        return [self._parse(Fixture, item, context=f"fixtures[{i}]") for i, item in enumerate(raw)]

    def element_summary(
        self,
        player_id: int,
        ttl_seconds: float = TTL_ELEMENT_SUMMARY,
        force_refresh: bool = False,
    ) -> ElementSummary:
        raw = self._get(
            f"/element-summary/{player_id}/",
            cache_key=f"element_summary_{player_id}",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return self._parse(ElementSummary, raw, context=f"element-summary/{player_id}")

    def event_live(
        self, gw: int, ttl_seconds: float = TTL_EVENT_LIVE, force_refresh: bool = False
    ) -> EventLive:
        raw = self._get(
            f"/event/{gw}/live/",
            cache_key=f"event_live_{gw}",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return self._parse(EventLive, raw, context=f"event/{gw}/live")

    def entry(
        self, entry_id: int, ttl_seconds: float = TTL_ENTRY, force_refresh: bool = False
    ) -> Entry:
        raw = self._get(
            f"/entry/{entry_id}/",
            cache_key=f"entry_{entry_id}",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return self._parse(Entry, raw, context=f"entry/{entry_id}")

    def entry_history(
        self, entry_id: int, ttl_seconds: float = TTL_ENTRY, force_refresh: bool = False
    ) -> EntryHistory:
        raw = self._get(
            f"/entry/{entry_id}/history/",
            cache_key=f"entry_history_{entry_id}",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return self._parse(EntryHistory, raw, context=f"entry/{entry_id}/history")

    def entry_picks(
        self,
        entry_id: int,
        gw: int,
        ttl_seconds: float = TTL_ENTRY,
        force_refresh: bool = False,
    ) -> EntryPicks:
        raw = self._get(
            f"/entry/{entry_id}/event/{gw}/picks/",
            cache_key=f"entry_picks_{entry_id}_{gw}",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return self._parse(EntryPicks, raw, context=f"entry/{entry_id}/event/{gw}/picks")

    def league_standings(
        self,
        league_id: int,
        page: int = 1,
        ttl_seconds: float = TTL_ENTRY,
        force_refresh: bool = False,
    ) -> LeagueStandings:
        raw = self._get(
            f"/leagues-classic/{league_id}/standings/",
            cache_key=f"league_standings_{league_id}_p{page}",
            ttl_seconds=ttl_seconds,
            params={"page_standings": page},
            force_refresh=force_refresh,
        )
        return self._parse(
            LeagueStandings, raw, context=f"leagues-classic/{league_id}/standings"
        )

    def entry_transfers(
        self, entry_id: int, ttl_seconds: float = TTL_ENTRY, force_refresh: bool = False
    ) -> list[Transfer]:
        raw = self._get(
            f"/entry/{entry_id}/transfers/",
            cache_key=f"entry_transfers_{entry_id}",
            ttl_seconds=ttl_seconds,
            force_refresh=force_refresh,
        )
        return [
            self._parse(Transfer, item, context=f"entry/{entry_id}/transfers[{i}]")
            for i, item in enumerate(raw)
        ]

    @staticmethod
    def _parse(model: type, raw: Any, *, context: str) -> Any:
        try:
            return model.model_validate(raw)
        except Exception as exc:  # pydantic.ValidationError, primarily
            raise SchemaDriftError(
                f"FPL API payload for {context!r} no longer matches fplscout.ingest.schemas."
                f" This usually means the season reset or a mid-season API change."
                f" Original error: {exc}"
            ) from exc
