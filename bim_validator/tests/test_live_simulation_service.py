from application.live_simulation_service import compute_slice


def test_compute_slice_covers_every_equipment_exactly_once_per_cycle():
    equipment_ids = [f"eq-{i}" for i in range(23)]
    ticks_per_cycle = 5

    seen: list[str] = []
    for tick_index in range(ticks_per_cycle):
        seen.extend(compute_slice(equipment_ids, tick_index, ticks_per_cycle))

    assert sorted(seen) == sorted(equipment_ids)
    assert len(seen) == len(equipment_ids)


def test_compute_slice_stable_regardless_of_tick_index_wraparound():
    equipment_ids = [f"eq-{i}" for i in range(10)]
    ticks_per_cycle = 4

    # tick_index 5 est équivalent à tick_index 1 (5 % 4 == 1) -- l'appelant
    # (run_zone) passe déjà tick_index % ticks_per_cycle, mais la fonction
    # elle-même doit rester cohérente si on lui passe un index brut.
    assert compute_slice(equipment_ids, 5 % ticks_per_cycle, ticks_per_cycle) == compute_slice(
        equipment_ids, 1, ticks_per_cycle
    )


def test_compute_slice_no_duplicates_within_a_single_tick():
    equipment_ids = [f"eq-{i}" for i in range(7)]
    slice_ids = compute_slice(equipment_ids, 0, 3)
    assert len(slice_ids) == len(set(slice_ids))


def test_compute_slice_empty_equipment_list():
    assert compute_slice([], 0, 10) == []


def test_compute_slice_single_tick_per_cycle_returns_everything():
    equipment_ids = [f"eq-{i}" for i in range(4)]
    assert compute_slice(equipment_ids, 0, 1) == equipment_ids
