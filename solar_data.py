from __future__ import annotations

from datetime import UTC, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pvlib
from timezonefinder import TimezoneFinder


VALID_INTERVALS = {1, 5, 15}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


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


def monthly_hourly_ghi_matrix(
    data: pd.DataFrame, latitude: float, longitude: float
) -> tuple[pd.DataFrame, str, float]:
    """Average clear-sky GHI by month and hour in local standard time."""
    timezone_name = TimezoneFinder().timezone_at(lat=latitude, lng=longitude) or "UTC"
    site_timezone = ZoneInfo(timezone_name)
    year = int(data.index[0].year)
    monthly_offsets = [
        datetime(year, month, 15, 12, tzinfo=UTC).astimezone(site_timezone).utcoffset()
        for month in range(1, 13)
    ]
    standard_offset = min(offset for offset in monthly_offsets if offset is not None)
    standard_timezone = timezone(standard_offset)

    local_index = data.index.tz_convert(standard_timezone)
    grouped = data["ghi"].groupby([local_index.month, local_index.hour]).mean()
    matrix = grouped.unstack(fill_value=0).reindex(index=range(1, 13), columns=range(24), fill_value=0)
    matrix.index = MONTH_NAMES
    matrix.index.name = "month"
    matrix.columns.name = "hour"
    return matrix, timezone_name, standard_offset.total_seconds() / 3600
