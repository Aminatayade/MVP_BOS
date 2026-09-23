from application.energy_aggregation import (
    bucket_daily_by_subcategory,
    densify_daily_subcategories,
    filter_by_plage,
    month_total_by_subcategory,
    window_duration_hours,
)


def _row(ts: str, value: float, gid: str = "eq-1") -> dict:
    return {"ts": ts, "value": value, "global_id": gid}


def test_filter_by_plage_toute_journee_keeps_everything():
    rows = [_row("2026-09-01 02:00:00.000000", 1.0), _row("2026-09-01 23:00:00.000000", 1.0)]
    assert filter_by_plage(rows, "toute_journee") == rows


def test_filter_by_plage_ouvrable_keeps_only_8h_to_19h():
    rows = [
        _row("2026-09-01 07:59:00.000000", 1.0),  # juste avant
        _row("2026-09-01 08:00:00.000000", 2.0),  # borne basse incluse
        _row("2026-09-01 18:59:00.000000", 3.0),  # dernière heure incluse
        _row("2026-09-01 19:00:00.000000", 4.0),  # borne haute exclue
    ]
    filtered = filter_by_plage(rows, "ouvrable")
    assert [r["value"] for r in filtered] == [2.0, 3.0]


def test_filter_by_plage_hors_ouvrable_is_the_complement():
    rows = [
        _row("2026-09-01 07:59:00.000000", 1.0),
        _row("2026-09-01 08:00:00.000000", 2.0),
        _row("2026-09-01 18:59:00.000000", 3.0),
        _row("2026-09-01 19:00:00.000000", 4.0),
    ]
    filtered = filter_by_plage(rows, "hors_ouvrable")
    assert [r["value"] for r in filtered] == [1.0, 4.0]


def test_filter_by_plage_does_not_drop_weekends():
    # 2026-09-05 est un samedi -- contrairement à l'occupation, la
    # consommation énergétique n'est jamais coupée le week-end.
    rows = [_row("2026-09-05 10:00:00.000000", 5.0)]
    assert filter_by_plage(rows, "ouvrable") == rows


def test_window_duration_hours_matches_each_plage():
    assert window_duration_hours("toute_journee") == 24
    assert window_duration_hours("ouvrable") == 11
    assert window_duration_hours("hors_ouvrable") == 13
    assert window_duration_hours("valeur_inconnue") == 24


def test_bucket_daily_by_subcategory_averages_and_scales_by_window():
    rows = [
        _row("2026-09-01 08:00:00.000000", 2.0, "eq-eclairage"),
        _row("2026-09-01 12:00:00.000000", 4.0, "eq-eclairage"),
        _row("2026-09-02 08:00:00.000000", 1.0, "eq-cvc"),
    ]
    subcategory_by_gid = {"eq-eclairage": "eclairage", "eq-cvc": "cvc"}
    daily = bucket_daily_by_subcategory(rows, subcategory_by_gid, plage="toute_journee")
    # moyenne (2.0, 4.0) = 3.0 kW * 24h = 72.0 kWh
    assert daily["2026-09-01"]["eclairage"] == 72.0
    assert daily["2026-09-02"]["cvc"] == 24.0
    assert "cvc" not in daily["2026-09-01"]


def test_bucket_daily_by_subcategory_ignores_unmapped_global_id():
    rows = [_row("2026-09-01 08:00:00.000000", 10.0, "eq-orphelin")]
    daily = bucket_daily_by_subcategory(rows, {}, plage="toute_journee")
    assert daily == {}


def test_densify_daily_subcategories_zero_fills_missing_pairs():
    daily = {"2026-09-01": {"eclairage": 5.0}}
    dense = densify_daily_subcategories(daily, ["eclairage", "cvc"], ["2026-09-01", "2026-09-02"])
    assert dense == {
        "2026-09-01": {"eclairage": 5.0, "cvc": 0.0},
        "2026-09-02": {"eclairage": 0.0, "cvc": 0.0},
    }


def test_month_total_by_subcategory_sums_across_days():
    daily = {
        "2026-09-01": {"eclairage": 5.0, "cvc": 10.0},
        "2026-09-02": {"eclairage": 3.0},
    }
    assert month_total_by_subcategory(daily) == {"eclairage": 8.0, "cvc": 10.0}
