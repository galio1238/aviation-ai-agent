# scoring.py — Phase 2
from __future__ import annotations

from dataclasses import dataclass, field
import math

# ---------------------------------------------------------------------------
# Weights — Phase 2.1 placeholder (equal weighting; tune after research)
# ---------------------------------------------------------------------------
W_UTILIZATION: float = 0.25
W_GROWTH: float = 0.25
W_UNMET_DEMAND: float = 0.25
W_CAPEX_PENALTY: float = 0.25


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class AirportMetrics:
    """Raw inputs collected from API clients for a single airport."""

    iata_code: str

    # Utilization inputs
    ops: float | None = None               # annual operations (departures + arrivals)
    rated_capacity: float | None = None    # annual capacity proxy from OpenAIP runway data

    # Growth inputs
    yearly_passenger_totals: list[int] = field(default_factory=list)  # oldest → newest

    # Unmet-demand proxy inputs
    avg_delay_minutes: float | None = None  # average arrival/departure delay (BTS on-time)
    load_factor: float | None = None        # passengers / available seats, 0–1
    capacity_capped_flag: bool = False      # True if ops are at or above rated_capacity
    cancellation_rate: float | None = None  # 1 - (departures_performed / departures_scheduled) from T-100

    # CAPEX penalty input
    recent_project_flag: bool = False       # True if major project completed in last 5 years


@dataclass
class ScoredAirport:
    """Scoring result for a single airport including per-component breakdown."""

    iata_code: str
    score: float                              # final composite score in [0, 1]

    # Per-component normalized contributions (None if data was unavailable)
    utilization_score: float | None = None
    growth_score: float | None = None
    unmet_demand_score: float | None = None
    capex_penalty: float | None = None       # subtracted from composite

    # Confidence: "high" | "medium" | "low"
    confidence: str = "high"

    # Original inputs for transparency / downstream display
    metrics: AirportMetrics | None = None


# ---------------------------------------------------------------------------
# 2.2 Component calculations
# ---------------------------------------------------------------------------

def calc_utilization_ratio(ops: float | None, rated_capacity: float | None) -> float | None:
    """Return ops / rated_capacity.

    Values >1.0 are valid and indicate over-capacity operation.
    Returns None when either input is missing or rated_capacity is zero.
    """
    if ops is None or rated_capacity is None:
        return None
    if rated_capacity <= 0:
        return None
    return ops / rated_capacity


def calc_passenger_growth(yearly_totals: list[int]) -> float | None:
    """Return 3-year CAGR over the last 4 data points (or fewer if unavailable).

    CAGR = (end / start) ^ (1 / years) - 1

    Uses the most recent 4 values so the result covers exactly 3 years.
    Returns None when fewer than 2 data points are present or start value is 0.
    """
    if len(yearly_totals) < 2:
        return None
    series = yearly_totals[-4:]          # at most 4 years: gives up to 3-year CAGR
    start, end = series[0], series[-1]
    years = len(series) - 1
    if start <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def calc_unmet_demand_proxy(
    delay_minutes: float | None,
    load_factor: float | None,
    capacity_capped_flag: bool,
    cancellation_rate: float | None = None,
) -> float | None:
    """Return a proxy score for unmet demand in [0, 1].

    IMPORTANT — this is a proxy, not a direct measurement. True unmet demand
    (passengers who wanted to fly but could not book) is not available in public
    datasets. This function uses up to four indirect signals:

      - delay_minutes: chronic delays indicate congestion / capacity strain
        (BTS on-time data; stub until CSV is downloaded)
      - cancellation_rate: 1 - (departures_performed / departures_scheduled) from
        BTS T-100; used as a substitute when delay_minutes is unavailable — high
        cancellation rates reflect the same operational strain as delays
      - load_factor: consistently high seat utilization suggests demand saturates supply
      - capacity_capped_flag: operations already at or above the runway/gate ceiling

    Each signal is independently clamped to [0, 1] then averaged over all available
    signals. A result of 1.0 means all indicators are at maximum strain; 0.0 means
    no congestion signal at all.

    Returns None only when ALL signals are unavailable.
    """
    components: list[float] = []

    if delay_minutes is not None:
        # 0 min → 0.0; 60+ min → 1.0 (linear, capped)
        components.append(min(delay_minutes / 60.0, 1.0))

    if cancellation_rate is not None:
        # 0% cancelled → 0.0; 20%+ cancelled → 1.0 (linear, capped)
        # 20% is an extreme threshold; typical healthy airports are <2%
        components.append(min(cancellation_rate / 0.20, 1.0))

    if load_factor is not None:
        # Clamp to [0, 1]; load factors above 1.0 are data errors
        components.append(max(0.0, min(load_factor, 1.0)))

    components.append(1.0 if capacity_capped_flag else 0.0)

    if not components:
        return None
    return sum(components) / len(components)


def calc_capex_penalty(recent_project_flag: bool) -> float:
    """Return 1.0 if a major infrastructure project was completed in the last 5 years,
    else 0.0.

    A recent large CAPEX spend reduces investment attractiveness: the airport may
    still be paying down debt, construction disruption may have suppressed metrics,
    and a follow-on project is politically harder to justify.
    """
    return 1.0 if recent_project_flag else 0.0


# ---------------------------------------------------------------------------
# 2.3 Normalization
# ---------------------------------------------------------------------------

def normalize_min_max(values: list[float]) -> list[float]:
    """Scale values to [0, 1] using min-max normalization.

    When all values are identical (range = 0), returns 0.5 for every entry
    rather than 0 or 1 — a neutral score that doesn't artificially reward or
    penalize any airport when there is no meaningful spread.

    Args:
        values: Non-empty list of finite floats. Must not contain None.

    Returns:
        List of the same length with each value in [0, 1].
    """
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    span = hi - lo
    return [(v - lo) / span for v in values]


def _normalize_component(raw: list[float | None]) -> list[float | None]:
    """Apply min-max normalization across an airport set, preserving None positions.

    Computes min/max only from non-None values, then maps each non-None entry
    to [0, 1]. None entries (missing data) are passed through unchanged so the
    composite scorer can handle them separately (e.g. drop from weighted average,
    lower confidence).
    """
    present = [(i, v) for i, v in enumerate(raw) if v is not None]
    if not present:
        return list(raw)

    indices, values = zip(*present)
    normalized = normalize_min_max(list(values))

    result: list[float | None] = list(raw)
    for idx, norm_val in zip(indices, normalized):
        result[idx] = norm_val
    return result


# ---------------------------------------------------------------------------
# 2.4 Composite scorer
# ---------------------------------------------------------------------------

def score_airports(airport_metrics_list: list[AirportMetrics]) -> list[ScoredAirport]:
    """Score and rank a set of airports by investment attractiveness.

    Pipeline:
      1. Compute raw component values per airport via the calc_* functions.
      2. Normalize each component across the full airport set (not per-airport)
         so scores reflect relative standing within the comparison group.
      3. Compute a weighted average of available positive components, then
         subtract the CAPEX penalty.
      4. Assign a confidence level based on data completeness.
      5. Return ScoredAirport list sorted by score descending.

    Missing components (None) are excluded from the weighted average and their
    weight is redistributed proportionally to the remaining components so the
    composite always sits in [0, 1] regardless of data gaps.

    CAPEX penalty is not normalized — it is a binary flag applied directly as a
    fixed deduction after the positive-component average is computed.
    """
    if not airport_metrics_list:
        return []

    # Step 1 — raw component values
    raw_util = [calc_utilization_ratio(m.ops, m.rated_capacity) for m in airport_metrics_list]
    raw_growth = [calc_passenger_growth(m.yearly_passenger_totals) for m in airport_metrics_list]
    raw_unmet = [
        calc_unmet_demand_proxy(
            m.avg_delay_minutes, m.load_factor, m.capacity_capped_flag, m.cancellation_rate
        )
        for m in airport_metrics_list
    ]
    raw_capex = [calc_capex_penalty(m.recent_project_flag) for m in airport_metrics_list]

    # Step 2 — normalize across airport set (None positions preserved)
    norm_util = _normalize_component(raw_util)
    norm_growth = _normalize_component(raw_growth)
    norm_unmet = _normalize_component(raw_unmet)
    # CAPEX is binary — no normalization

    results: list[ScoredAirport] = []

    for i, m in enumerate(airport_metrics_list):
        u, g, d, c = norm_util[i], norm_growth[i], norm_unmet[i], raw_capex[i]

        # Step 3 — weighted average over present positive components
        weighted = [
            (W_UTILIZATION, u),
            (W_GROWTH, g),
            (W_UNMET_DEMAND, d),
        ]
        active = [(w, v) for w, v in weighted if v is not None]

        if active:
            weight_sum = sum(w for w, _ in active)
            base_score = sum(w * v for w, v in active) / weight_sum
        else:
            base_score = 0.0

        score = max(0.0, base_score - W_CAPEX_PENALTY * c)

        # Step 4 — confidence
        none_count = sum(1 for v in [u, g, d] if v is None)
        sparse_growth = len(m.yearly_passenger_totals) < 2

        if none_count == 0 and not sparse_growth:
            confidence = "high"
        elif none_count >= 2 or sparse_growth:
            confidence = "low"
        else:
            confidence = "medium"

        results.append(ScoredAirport(
            iata_code=m.iata_code,
            score=round(score, 4),
            utilization_score=round(u, 4) if u is not None else None,
            growth_score=round(g, 4) if g is not None else None,
            unmet_demand_score=round(d, 4) if d is not None else None,
            capex_penalty=c,
            confidence=confidence,
            metrics=m,
        ))

    # Step 5 — sort by score descending
    results.sort(key=lambda s: s.score, reverse=True)
    return results
