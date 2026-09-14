import pytest

from sweep_utils import generate_sweep_thresholds, threshold_label


def test_sweep_thresholds_include_both_limits():
    assert generate_sweep_thresholds(100, 200, 10) == [
        100.0, 110.0, 120.0, 130.0, 140.0, 150.0,
        160.0, 170.0, 180.0, 190.0, 200.0,
    ]


def test_sweep_includes_maximum_when_step_does_not_divide_range():
    assert generate_sweep_thresholds(100, 200, 30) == [
        100.0, 130.0, 160.0, 190.0, 200.0,
    ]


@pytest.mark.parametrize(
    "minimum,maximum,step",
    [(200, 100, 10), (100, 200, 0), (-1, 100, 10), (100, 1401, 10)],
)
def test_invalid_sweep_inputs_are_rejected(minimum, maximum, step):
    with pytest.raises(ValueError):
        generate_sweep_thresholds(minimum, maximum, step)


def test_excessive_scenario_count_is_rejected():
    with pytest.raises(ValueError, match="101 scenarios"):
        generate_sweep_thresholds(100, 200, 1)


def test_threshold_label_is_file_name_safe():
    assert threshold_label(100.0) == "100"
    assert threshold_label(112.5) == "112.5"
