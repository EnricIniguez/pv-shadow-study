from __future__ import annotations

import pandas as pd
import pvlib


VALID_INTERVALS = {1, 5, 15}


def generate_annual_solar_data(
    latitude: float,
    longitude: float,
    year: int,
    interval_minutes: int,
    ghi_threshold: float,
) -> pd.DataFrame:
    """Return annual solar position and Ineichen clear-sky irradiance in UTC."""
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    if interval_minutes not in VALID_INTERVALS:
        raise ValueError("Time step must be 1, 5 or 15 minutes.")
    if ghi_threshold < 0:
        raise ValueError("GHI threshold cannot be negative.")

    times = pd.date_range(
        start=f"{year}-01-01 00:00",
        end=f"{year + 1}-01-01 00:00",
        freq=f"{interval_minutes}min",
        inclusive="left",
        tz="UTC",
    )

    location = pvlib.location.Location(latitude, longitude, tz="UTC")
    solar_position = location.get_solarposition(times)
    clear_sky = location.get_clearsky(times, model="ineichen")

    result = pd.DataFrame(index=times)
    result.index.name = "timestamp_utc"
    result["apparent_elevation"] = solar_position["apparent_elevation"]
    result["azimuth"] = solar_position["azimuth"]
    result["ghi"] = clear_sky["ghi"]
    result["dni"] = clear_sky["dni"]
    result["dhi"] = clear_sky["dhi"]
    result["above_ghi_threshold"] = result["ghi"] >= ghi_threshold
    return result

