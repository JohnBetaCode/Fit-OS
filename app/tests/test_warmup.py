from fitos.training.warmup import needs_warmup, warmup_sets

GYM = {"barbell_step_kg": 2.5, "machine_step_kg": 5, "dumbbell_kg": [2.5 * i for i in range(1, 21)]}


def test_barbell_ramp_starts_with_the_empty_bar():
    assert warmup_sets(80, "total", "barbell", GYM) == [(10, 20.0), (10, 35.0), (5, 50.0), (2, 67.5)]


def test_light_barbell_has_no_heavy_single_and_no_duplicates():
    assert warmup_sets(40, "total", "barbell", GYM) == [(10, 20.0), (5, 25.0)]
    assert warmup_sets(25, "total", "barbell", GYM) == [(10, 20.0)]


def test_dumbbells_round_down_to_pairs_you_own():
    assert warmup_sets(22.5, "per_hand", "dumbbell", GYM) == [(10, 10.0), (5, 12.5)]


def test_machine_steps_and_calibration():
    assert warmup_sets(50, "total", "machine", GYM) == [(10, 20.0), (5, 30.0), (2, 40.0)]
    assert warmup_sets(None, "total", "barbell", GYM) == [(10, None), (5, None)]


def test_only_first_compound_loaded_lift_per_muscle():
    assert needs_warmup("compound", "total", "chest", set())
    assert not needs_warmup("compound", "total", "chest", {"chest"})
    assert not needs_warmup("isolation", "total", "chest", set())
    assert not needs_warmup("compound", "bodyweight", "chest", set())
