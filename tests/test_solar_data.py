import pytest

from solar_data import generate_annual_solar_data


def test_annual_15_minute_row_count():
    result = generate_annual_solar_data(41.3874, 2.1686, 2025, 15, 100)
    assert len(result) == 365 * 24 * 4
    assert {"apparent_elevation", "azimuth", "ghi", "dni", "dhi", "above_ghi_threshold"}.issubset(result.columns)


@pytest.mark.parametrize("interval", [1, 5, 15])
def test_supported_intervals(interval):
    result = generate_annual_solar_data(0, 0, 2025, interval, 0)
    assert len(result) == 365 * 24 * 60 // interval


def test_invalid_coordinates():
    with pytest.raises(ValueError):
        generate_annual_solar_data(91, 0, 2025, 15, 100)

