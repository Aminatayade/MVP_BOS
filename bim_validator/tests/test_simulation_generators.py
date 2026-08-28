import random
from datetime import datetime, timezone

from application.simulation_generators import (
    cumulative_daily_profile,
    generate_metric_history,
    sparse_event_log,
)
from domain.entities import MetricSpec

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def test_cumulative_daily_profile_never_decreases():
    metric = MetricSpec(name="energy_cumulative_kwh", unit="kWh", generator="cumulative_daily_profile",
                         params={"daily_growth": 40.0, "peak_hours": [8, 19]})
    messages = cumulative_daily_profile(
        global_id="eq-1", metric=metric, history_days=3, points_per_day=24,
        now=NOW, rng=random.Random(42), source="test",
    )
    values = [m.value for m in messages]
    assert values == sorted(values)
    assert values[0] >= 0
    assert values[-1] > values[0]


def test_sparse_event_log_mostly_default_value():
    metric = MetricSpec(name="state", unit="état", generator="sparse_event_log",
                         params={"default_value": 0.0, "event_value": 1.0, "events_per_week": 1.0})
    messages = sparse_event_log(
        global_id="eq-2", metric=metric, history_days=7, points_per_day=24,
        now=NOW, rng=random.Random(1), source="test",
    )
    event_count = sum(1 for m in messages if m.value == 1.0)
    # events_per_week=1 sur 7 jours : on attend un événement rare, pas un flot.
    assert event_count < len(messages) * 0.1


def test_generate_metric_history_unknown_generator_returns_empty():
    metric = MetricSpec(name="mystery", unit="", generator="does_not_exist", params={})
    messages = generate_metric_history(
        global_id="eq-3", metric=metric, history_days=7, points_per_day=24,
        now=NOW, rng=random.Random(0),
    )
    assert messages == []


def test_generate_metric_history_point_count_matches_days_times_points_per_day():
    metric = MetricSpec(name="temperature", unit="°C", generator="day_night_cycle",
                         params={"base": 21.0, "amplitude": 2.0, "noise": 0.5})
    messages = generate_metric_history(
        global_id="eq-4", metric=metric, history_days=7, points_per_day=24,
        now=NOW, rng=random.Random(0),
    )
    assert len(messages) == 7 * 24
    assert all(m.global_id == "eq-4" and m.metric == "temperature" for m in messages)
