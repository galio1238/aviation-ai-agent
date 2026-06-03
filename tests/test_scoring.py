# tests/test_scoring.py — Phase 2.5
import pytest
from scoring import (
    AirportMetrics,
    ScoredAirport,
    calc_utilization_ratio,
    calc_passenger_growth,
    calc_unmet_demand_proxy,
    calc_capex_penalty,
    normalize_min_max,
    score_airports,
)


# ---------------------------------------------------------------------------
# 2.5.1  Utilization ratio edge cases
# ---------------------------------------------------------------------------

class TestCalcUtilizationRatio:
    def test_normal(self):
        assert calc_utilization_ratio(80_000, 100_000) == pytest.approx(0.8)

    def test_at_capacity(self):
        assert calc_utilization_ratio(100_000, 100_000) == pytest.approx(1.0)

    def test_over_capacity(self):
        result = calc_utilization_ratio(130_000, 100_000)
        assert result == pytest.approx(1.3)
        assert result > 1.0  # valid; over-capacity is intentionally allowed

    def test_zero_ops(self):
        assert calc_utilization_ratio(0, 100_000) == pytest.approx(0.0)

    def test_zero_capacity_returns_none(self):
        assert calc_utilization_ratio(50_000, 0) is None

    def test_none_ops_returns_none(self):
        assert calc_utilization_ratio(None, 100_000) is None

    def test_none_capacity_returns_none(self):
        assert calc_utilization_ratio(50_000, None) is None

    def test_both_none_returns_none(self):
        assert calc_utilization_ratio(None, None) is None


# ---------------------------------------------------------------------------
# 2.5.2  Passenger growth — 3-year CAGR
# ---------------------------------------------------------------------------

class TestCalcPassengerGrowth:
    def test_flat_series_returns_zero(self):
        result = calc_passenger_growth([1_000_000] * 4)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_growing_series(self):
        # 10% CAGR: 100 → 110 → 121 → 133.1
        totals = [1_000_000, 1_100_000, 1_210_000, 1_331_000]
        result = calc_passenger_growth(totals)
        assert result == pytest.approx(0.10, rel=1e-3)

    def test_shrinking_series(self):
        totals = [1_331_000, 1_210_000, 1_100_000, 1_000_000]
        result = calc_passenger_growth(totals)
        assert result < 0.0

    def test_two_points_gives_one_year_cagr(self):
        result = calc_passenger_growth([1_000_000, 1_050_000])
        assert result == pytest.approx(0.05, rel=1e-6)

    def test_uses_only_last_four_points(self):
        # extra early years shouldn't affect the result
        long_series  = [500_000, 600_000, 700_000, 1_000_000, 1_100_000, 1_210_000, 1_331_000]
        short_series = [1_000_000, 1_100_000, 1_210_000, 1_331_000]
        assert calc_passenger_growth(long_series) == pytest.approx(
            calc_passenger_growth(short_series), rel=1e-9
        )

    def test_single_year_returns_none(self):
        assert calc_passenger_growth([1_000_000]) is None

    def test_empty_returns_none(self):
        assert calc_passenger_growth([]) is None

    def test_zero_start_returns_none(self):
        assert calc_passenger_growth([0, 1_000_000]) is None


# ---------------------------------------------------------------------------
# Helper: normalize_min_max
# ---------------------------------------------------------------------------

class TestNormalizeMinMax:
    def test_normal_case(self):
        result = normalize_min_max([0.0, 50.0, 100.0])
        assert result == pytest.approx([0.0, 0.5, 1.0])

    def test_all_same_returns_half(self):
        result = normalize_min_max([42.0, 42.0, 42.0])
        assert result == pytest.approx([0.5, 0.5, 0.5])

    def test_empty_returns_empty(self):
        assert normalize_min_max([]) == []

    def test_single_value_returns_half(self):
        assert normalize_min_max([7.0]) == pytest.approx([0.5])

    def test_output_bounds(self):
        values = [1.0, 3.0, 2.0, 5.0, 4.0]
        result = normalize_min_max(values)
        assert min(result) == pytest.approx(0.0)
        assert max(result) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2.5.3  score_airports — determinism and sort order
# ---------------------------------------------------------------------------

class TestScoreAirportsDeterminism:
    def _make_set(self):
        return [
            AirportMetrics("BOS", ops=450_000, rated_capacity=500_000,
                           yearly_passenger_totals=[30_000_000, 32_000_000, 34_000_000, 36_000_000],
                           avg_delay_minutes=18, load_factor=0.88, cancellation_rate=0.03),
            AirportMetrics("PVD", ops=80_000, rated_capacity=200_000,
                           yearly_passenger_totals=[4_000_000, 3_800_000, 3_700_000, 3_600_000],
                           avg_delay_minutes=9, load_factor=0.72, cancellation_rate=0.01),
            AirportMetrics("MHT", ops=30_000, rated_capacity=120_000,
                           yearly_passenger_totals=[1_200_000, 1_100_000],
                           avg_delay_minutes=7, load_factor=0.65, cancellation_rate=0.01),
        ]

    def test_same_input_same_output(self):
        first  = score_airports(self._make_set())
        second = score_airports(self._make_set())
        assert [s.iata_code for s in first] == [s.iata_code for s in second]
        assert [s.score for s in first]     == [s.score for s in second]

    def test_sorted_descending(self):
        results = score_airports(self._make_set())
        scores = [s.score for s in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_input_returns_empty(self):
        assert score_airports([]) == []

    def test_single_airport_returns_one_result(self):
        airport = AirportMetrics("BOS", ops=450_000, rated_capacity=500_000,
                                 yearly_passenger_totals=[30_000_000, 36_000_000])
        results = score_airports([airport])
        assert len(results) == 1
        assert results[0].iata_code == "BOS"

    def test_score_in_zero_one_range(self):
        results = score_airports(self._make_set())
        for s in results:
            assert 0.0 <= s.score <= 1.0


# ---------------------------------------------------------------------------
# 2.5.4  Confidence level
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_high_when_all_data_present(self):
        airport = AirportMetrics(
            "BOS", ops=450_000, rated_capacity=500_000,
            yearly_passenger_totals=[30_000_000, 32_000_000, 34_000_000],
            avg_delay_minutes=18, load_factor=0.88, cancellation_rate=0.03,
        )
        result = score_airports([airport])[0]
        assert result.confidence == "high"

    def test_low_on_single_year_passengers(self):
        airport = AirportMetrics(
            "PVD", ops=80_000, rated_capacity=200_000,
            yearly_passenger_totals=[4_000_000],   # only 1 year
            avg_delay_minutes=9, load_factor=0.72,
        )
        result = score_airports([airport])[0]
        assert result.confidence == "low"

    def test_low_on_empty_passengers(self):
        airport = AirportMetrics(
            "MHT", ops=30_000, rated_capacity=120_000,
            yearly_passenger_totals=[],             # no data at all
        )
        result = score_airports([airport])[0]
        assert result.confidence == "low"

    def test_medium_on_one_missing_component(self):
        # ops=None means utilization will be None — one missing component
        airport = AirportMetrics(
            "BDL", ops=None, rated_capacity=None,
            yearly_passenger_totals=[5_000_000, 5_200_000, 5_400_000],
            avg_delay_minutes=12, load_factor=0.80,
        )
        result = score_airports([airport])[0]
        assert result.confidence == "medium"

    def test_low_on_two_missing_components(self):
        # ops=None (util missing) and no delay/load (unmet demand degrades to just the flag)
        # growth=None from empty passenger list → two components None
        airport = AirportMetrics(
            "ORH", ops=None, rated_capacity=None,
            yearly_passenger_totals=[],             # growth also None
        )
        result = score_airports([airport])[0]
        assert result.confidence == "low"


# ---------------------------------------------------------------------------
# 2.5.5  Regression — New England airport ranking
# ---------------------------------------------------------------------------

class TestNewEnglandRegression:
    """
    Q1 target: "Which New England airports are strong candidates for terminal expansion?"

    Expected: BOS ranks first (high utilization, growing, congested).
              Small underused airports (ORH, PWM) rank last.
              BDL is penalized by its recent terminal project.
    """

    def _airports(self):
        return [
            AirportMetrics(
                "BOS", ops=455_000, rated_capacity=500_000,
                yearly_passenger_totals=[33_000_000, 35_000_000, 37_000_000, 40_000_000],
                avg_delay_minutes=19, load_factor=0.89, cancellation_rate=0.03,
                recent_project_flag=False,
            ),
            AirportMetrics(
                "PVD", ops=82_000, rated_capacity=220_000,
                yearly_passenger_totals=[4_200_000, 4_100_000, 3_900_000, 3_700_000],
                avg_delay_minutes=10, load_factor=0.74, cancellation_rate=0.01,
                recent_project_flag=False,
            ),
            AirportMetrics(
                "MHT", ops=28_000, rated_capacity=130_000,
                yearly_passenger_totals=[1_300_000, 1_200_000, 1_100_000, 1_050_000],
                avg_delay_minutes=8, load_factor=0.68, cancellation_rate=0.01,
                recent_project_flag=False,
            ),
            AirportMetrics(
                "BDL", ops=195_000, rated_capacity=280_000,
                yearly_passenger_totals=[6_000_000, 6_200_000, 6_400_000, 6_600_000],
                avg_delay_minutes=14, load_factor=0.79, cancellation_rate=0.02,
                recent_project_flag=True,   # recent terminal rebuild — penalized
            ),
            AirportMetrics(
                "PWM", ops=18_000, rated_capacity=100_000,
                yearly_passenger_totals=[1_900_000, 1_850_000, 1_800_000, 1_750_000],
                avg_delay_minutes=7, load_factor=0.66, cancellation_rate=0.01,
                recent_project_flag=False,
            ),
            AirportMetrics(
                "ORH", ops=5_000, rated_capacity=80_000,
                yearly_passenger_totals=[200_000, 190_000, 185_000, 180_000],
                avg_delay_minutes=5, load_factor=0.55, cancellation_rate=0.01,
                recent_project_flag=False,
            ),
        ]

    def test_bos_ranks_first(self):
        results = score_airports(self._airports())
        assert results[0].iata_code == "BOS"

    def test_orh_ranks_last(self):
        results = score_airports(self._airports())
        assert results[-1].iata_code == "ORH"

    def test_bdl_capex_penalty_reduces_score(self):
        # Verify the penalty is actually applied — BDL with flag off must score higher
        results_with = score_airports(self._airports())
        bdl_penalized = next(s for s in results_with if s.iata_code == "BDL")

        airports_no_penalty = [
            AirportMetrics(**{**a.__dict__, "recent_project_flag": False})
            if a.iata_code == "BDL" else a
            for a in self._airports()
        ]
        results_without = score_airports(airports_no_penalty)
        bdl_unpenalized = next(s for s in results_without if s.iata_code == "BDL")

        assert bdl_penalized.score < bdl_unpenalized.score

    def test_all_have_per_component_breakdown(self):
        for s in score_airports(self._airports()):
            assert s.utilization_score  is not None
            assert s.growth_score       is not None
            assert s.unmet_demand_score is not None
            assert s.capex_penalty      is not None

    def test_all_confidence_high(self):
        for s in score_airports(self._airports()):
            assert s.confidence == "high"
