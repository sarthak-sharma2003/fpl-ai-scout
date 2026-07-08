"""Pydantic models for every FPL API payload we consume.

Deliberately strict (`extra="forbid"`) on top-level fields: the FPL API is expected to
drift when the 2026/27 season resets (new/renamed/removed fields, changed scoring
config). We want ingestion to fail loudly the day that happens rather than silently
dropping data — see plan §0 risk table.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

STRICT = ConfigDict(extra="forbid")
LENIENT = ConfigDict(extra="allow")  # for stat-blob dicts that are genuinely open-ended


# ---------------------------------------------------------------------------
# bootstrap-static/
# ---------------------------------------------------------------------------


class Chip(BaseModel):
    model_config = STRICT

    id: int
    name: str
    number: int
    start_event: int
    stop_event: int
    chip_type: str
    overrides: dict = {}


class ElementType(BaseModel):
    model_config = STRICT

    id: int
    plural_name: str
    plural_name_short: str
    singular_name: str
    singular_name_short: str
    squad_select: int
    squad_min_select: int | None = None
    squad_max_select: int | None = None
    squad_min_play: int
    squad_max_play: int
    ui_shirt_specific: bool
    sub_positions_locked: list[int] = []
    element_count: int


class Team(BaseModel):
    model_config = STRICT

    id: int
    code: int
    name: str
    short_name: str
    strength: int
    played: int
    win: int
    draw: int
    loss: int
    points: int
    position: int
    form: str | None = None
    team_division: int | None = None
    unavailable: bool
    link_url: str = ""
    pulse_id: int
    strength_overall_home: int
    strength_overall_away: int
    strength_attack_home: int
    strength_attack_away: int
    strength_defence_home: int
    strength_defence_away: int


class ChipPlay(BaseModel):
    model_config = STRICT

    chip_name: str
    num_played: int


class TopElementInfo(BaseModel):
    model_config = STRICT

    id: int
    points: int


class Event(BaseModel):
    model_config = STRICT

    id: int
    name: str
    deadline_time: datetime
    deadline_time_epoch: int
    deadline_time_game_offset: int
    release_time: datetime | None = None
    average_entry_score: int
    finished: bool
    data_checked: bool
    highest_scoring_entry: int | None = None
    highest_score: int | None = None
    is_previous: bool
    is_current: bool
    is_next: bool
    cup_leagues_created: bool
    h2h_ko_matches_created: bool
    can_enter: bool
    can_manage: bool
    released: bool
    ranked_count: int
    overrides: dict = {}
    chip_plays: list[ChipPlay] = []
    most_selected: int | None = None
    most_transferred_in: int | None = None
    top_element: int | None = None
    top_element_info: TopElementInfo | None = None
    transfers_made: int
    most_captained: int | None = None
    most_vice_captained: int | None = None


class Element(BaseModel):
    """A player. Every field the API currently returns for bootstrap-static elements."""

    model_config = STRICT

    id: int
    code: int
    first_name: str
    second_name: str
    web_name: str
    known_name: str | None = None
    team: int
    team_code: int
    element_type: int
    squad_number: int | None = None
    birth_date: str | None = None
    region: int | None = None
    opta_code: str | None = None
    has_temporary_code: bool = False
    removed: bool = False

    now_cost: int
    cost_change_event: int
    cost_change_event_fall: int
    cost_change_start: int
    cost_change_start_fall: int
    price_change_percent: float | None = None
    now_cost_rank: int | None = None
    now_cost_rank_type: int | None = None

    status: str
    news: str = ""
    news_added: datetime | None = None
    chance_of_playing_next_round: int | None = None
    chance_of_playing_this_round: int | None = None
    can_select: bool = True
    can_transact: bool = True
    special: bool = False

    total_points: int
    event_points: int
    points_per_game: str
    points_per_game_rank: int | None = None
    points_per_game_rank_type: int | None = None
    form: str
    form_rank: int | None = None
    form_rank_type: int | None = None
    ep_next: str | None = None
    ep_this: str | None = None
    value_form: str
    value_season: str
    dreamteam_count: int
    in_dreamteam: bool

    minutes: int
    starts: int
    starts_per_90: float
    goals_scored: int
    assists: int
    clean_sheets: int
    clean_sheets_per_90: float
    goals_conceded: int
    goals_conceded_per_90: float
    own_goals: int
    penalties_saved: int
    penalties_missed: int
    yellow_cards: int
    red_cards: int
    saves: int
    saves_per_90: float
    bonus: int
    bps: int

    influence: str
    influence_rank: int | None = None
    influence_rank_type: int | None = None
    creativity: str
    creativity_rank: int | None = None
    creativity_rank_type: int | None = None
    threat: str
    threat_rank: int | None = None
    threat_rank_type: int | None = None
    ict_index: str
    ict_index_rank: int | None = None
    ict_index_rank_type: int | None = None

    expected_goals: str
    expected_assists: str
    expected_goal_involvements: str
    expected_goals_conceded: str
    expected_goals_per_90: float
    expected_assists_per_90: float
    expected_goal_involvements_per_90: float
    expected_goals_conceded_per_90: float

    defensive_contribution: int
    defensive_contribution_per_90: float
    clearances_blocks_interceptions: int
    recoveries: int
    tackles: int

    selected_by_percent: str
    selected_rank: int | None = None
    selected_rank_type: int | None = None
    transfers_in: int
    transfers_in_event: int
    transfers_out: int
    transfers_out_event: int

    penalties_order: int | None = None
    penalties_text: str = ""
    direct_freekicks_order: int | None = None
    direct_freekicks_text: str = ""
    corners_and_indirect_freekicks_order: int | None = None
    corners_and_indirect_freekicks_text: str = ""

    photo: str
    team_join_date: str | None = None
    scout_news_link: str | None = None
    scout_risks: list | None = None


class GameSettings(BaseModel):
    """Loose model: this blob is large and rules can be added; we only assert on the
    specific keys we actually depend on (squad rules) and pass the rest through."""

    model_config = LENIENT

    squad_squadplay: int
    squad_squadsize: int
    squad_team_limit: int
    squad_total_spend: int
    element_sell_at_purchase_price: bool
    transfers_sell_on_fee: float


class BootstrapStatic(BaseModel):
    model_config = STRICT

    chips: list[Chip]
    events: list[Event]
    game_settings: GameSettings
    game_config: dict = {}
    phases: list[dict] = []
    element_types: list[ElementType]
    teams: list[Team]
    total_players: int
    element_stats: list[dict] = []
    elements: list[Element]


# ---------------------------------------------------------------------------
# fixtures/
# ---------------------------------------------------------------------------


class FixtureStatValue(BaseModel):
    model_config = STRICT

    value: int
    element: int


class FixtureStat(BaseModel):
    model_config = STRICT

    identifier: str
    a: list[FixtureStatValue] = []
    h: list[FixtureStatValue] = []


class Fixture(BaseModel):
    model_config = STRICT

    code: int
    event: int | None = None
    finished: bool
    finished_provisional: bool
    id: int
    kickoff_time: datetime | None = None
    minutes: int
    provisional_start_time: bool
    started: bool | None = None
    team_a: int
    team_a_score: int | None = None
    team_h: int
    team_h_score: int | None = None
    stats: list[FixtureStat] = []
    team_h_difficulty: int
    team_a_difficulty: int
    pulse_id: int


# ---------------------------------------------------------------------------
# element-summary/{id}/
# ---------------------------------------------------------------------------


class ElementSummaryHistory(BaseModel):
    """One row per match played by this player, current season."""

    model_config = STRICT

    element: int
    fixture: int
    opponent_team: int
    total_points: int
    was_home: bool
    kickoff_time: datetime
    team_h_score: int | None = None
    team_a_score: int | None = None
    round: int
    modified: bool
    minutes: int
    goals_scored: int
    assists: int
    clean_sheets: int
    goals_conceded: int
    own_goals: int
    penalties_saved: int
    penalties_missed: int
    yellow_cards: int
    red_cards: int
    saves: int
    bonus: int
    bps: int
    influence: str
    creativity: str
    threat: str
    ict_index: str
    starts: int
    expected_goals: str
    expected_assists: str
    expected_goal_involvements: str
    expected_goals_conceded: str
    defensive_contribution: int
    clearances_blocks_interceptions: int
    recoveries: int
    tackles: int
    value: int
    transfers_balance: int
    selected: int
    transfers_in: int
    transfers_out: int


class ElementSummaryHistoryPast(BaseModel):
    """One row per past season for this player."""

    model_config = STRICT

    season_name: str
    element_code: int
    start_cost: int
    end_cost: int
    total_points: int
    minutes: int
    goals_scored: int
    assists: int
    clean_sheets: int
    goals_conceded: int
    own_goals: int
    penalties_saved: int
    penalties_missed: int
    yellow_cards: int
    red_cards: int
    saves: int
    bonus: int
    bps: int
    influence: str
    creativity: str
    threat: str
    ict_index: str
    clearances_blocks_interceptions: int
    recoveries: int
    tackles: int
    defensive_contribution: int
    starts: int
    expected_goals: str
    expected_assists: str
    expected_goal_involvements: str
    expected_goals_conceded: str


class ElementSummaryFixture(BaseModel):
    """Upcoming fixture entry — loose, sparse fields depending on scheduling state."""

    model_config = LENIENT

    id: int
    event: int | None = None
    is_home: bool | None = None
    difficulty: int | None = None
    team_h: int | None = None
    team_a: int | None = None


class ElementSummary(BaseModel):
    model_config = STRICT

    fixtures: list[ElementSummaryFixture]
    history: list[ElementSummaryHistory]
    history_past: list[ElementSummaryHistoryPast]


# ---------------------------------------------------------------------------
# event/{gw}/live/
# ---------------------------------------------------------------------------


class LiveStats(BaseModel):
    model_config = STRICT

    minutes: int
    goals_scored: int
    assists: int
    clean_sheets: int
    goals_conceded: int
    own_goals: int
    penalties_saved: int
    penalties_missed: int
    yellow_cards: int
    red_cards: int
    saves: int
    bonus: int
    bps: int
    influence: str
    creativity: str
    threat: str
    ict_index: str
    clearances_blocks_interceptions: int
    recoveries: int
    tackles: int
    defensive_contribution: int
    starts: int
    expected_goals: str
    expected_assists: str
    expected_goal_involvements: str
    expected_goals_conceded: str
    total_points: int
    in_dreamteam: bool
    played: bool = True


class LiveExplainStat(BaseModel):
    model_config = LENIENT

    identifier: str
    points: int
    value: int
    points_modification: int


class LiveExplain(BaseModel):
    model_config = STRICT

    fixture: int
    stats: list[LiveExplainStat]


class LiveElement(BaseModel):
    model_config = STRICT

    id: int
    modified: bool
    stats: LiveStats
    explain: list[LiveExplain]


class EventLive(BaseModel):
    model_config = STRICT

    elements: list[LiveElement]


# ---------------------------------------------------------------------------
# entry/{id}/, entry/{id}/history/, entry/{id}/event/{gw}/picks/, entry/{id}/transfers/
# ---------------------------------------------------------------------------


class EntryLeagues(BaseModel):
    model_config = LENIENT

    classic: list[dict] = []
    h2h: list[dict] = []
    cup: dict = {}
    cup_matches: list[dict] = []


class Entry(BaseModel):
    model_config = STRICT

    id: int
    joined_time: str | None = None
    started_event: int
    favourite_team: int | None = None
    player_first_name: str
    player_last_name: str
    player_region_id: int | None = None
    player_region_name: str | None = None
    player_region_iso_code_short: str | None = None
    player_region_iso_code_long: str | None = None
    years_active: int
    summary_overall_points: int
    summary_overall_rank: int | None = None
    summary_event_points: int
    summary_event_rank: int | None = None
    current_event: int | None = None
    leagues: EntryLeagues
    name: str
    name_change_blocked: bool
    entered_events: list[int]
    kit: str | None = None
    last_deadline_bank: int
    last_deadline_value: int
    last_deadline_total_transfers: int
    club_badge_src: str | None = None


class EntryHistoryCurrent(BaseModel):
    model_config = STRICT

    event: int
    points: int
    total_points: int
    rank: int | None = None
    rank_sort: int | None = None
    overall_rank: int | None = None
    percentile_rank: int | None = None
    bank: int
    value: int
    event_transfers: int
    event_transfers_cost: int
    points_on_bench: int


class EntryHistoryPast(BaseModel):
    model_config = STRICT

    season_name: str
    total_points: int
    rank: int | None = None


class EntryHistoryChip(BaseModel):
    model_config = STRICT

    name: str
    time: str
    event: int


class EntryHistory(BaseModel):
    model_config = STRICT

    current: list[EntryHistoryCurrent]
    past: list[EntryHistoryPast]
    chips: list[EntryHistoryChip]


class Pick(BaseModel):
    model_config = STRICT

    element: int
    position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool
    element_type: int


class AutomaticSub(BaseModel):
    model_config = STRICT

    entry: int
    element_in: int
    element_out: int
    event: int


class EntryPicks(BaseModel):
    model_config = STRICT

    active_chip: str | None = None
    automatic_subs: list[AutomaticSub]
    entry_history: EntryHistoryCurrent
    picks: list[Pick]


class Transfer(BaseModel):
    model_config = STRICT

    element_in: int
    element_in_cost: int
    element_out: int
    element_out_cost: int
    entry: int
    event: int
    time: datetime
