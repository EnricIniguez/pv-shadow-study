"""Annual ground-shadow envelopes on a local East/North plane."""

from __future__ import annotations

from datetime import timedelta
from math import cos, pi, radians

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from object_geometry import cuboid_vertices


EARTH_RADIUS_M = 6_378_137.0


def latlon_to_local(
    latitude: float, longitude: float, reference_latitude: float, reference_longitude: float
) -> tuple[float, float]:
    """Convert a nearby WGS84 point to local East/North metres."""
    north = radians(latitude - reference_latitude) * EARTH_RADIUS_M
    east = (
        radians(longitude - reference_longitude)
        * EARTH_RADIUS_M
        * cos(radians(reference_latitude))
    )
    return east, north


def local_to_latlon(
    east: float, north: float, reference_latitude: float, reference_longitude: float
) -> tuple[float, float]:
    """Convert local East/North metres back to a nearby WGS84 point."""
    latitude = reference_latitude + north / EARTH_RADIUS_M * 180 / pi
    longitude = reference_longitude + (
        east / (EARTH_RADIUS_M * cos(radians(reference_latitude))) * 180 / pi
    )
    return latitude, longitude


def shadow_shift(height: float, solar_elevation: float, solar_azimuth: float) -> np.ndarray:
    """Return the ground displacement of a point at ``height`` metres."""
    elevation = radians(solar_elevation)
    azimuth = radians(solar_azimuth)
    horizontal_per_vertical = 1.0 / np.tan(elevation)
    return np.array([
        -height * np.sin(azimuth) * horizontal_per_vertical,
        -height * np.cos(azimuth) * horizontal_per_vertical,
    ])


def cuboid_shadow_polygon(item: dict, elevation: float, azimuth: float, ref_lat: float, ref_lon: float) -> Polygon:
    """Return one cuboid's complete projected ground shadow."""
    origin_east, origin_north = latlon_to_local(
        item["latitude"], item["longitude"], ref_lat, ref_lon
    )
    vertices = cuboid_vertices(
        item["length_x_m"], item["width_y_m"], item["height_z_m"], item["azimuth_deg"]
    ).copy()
    vertices[:, 0] += origin_east
    vertices[:, 1] += origin_north
    elevation_rad = radians(elevation)
    azimuth_rad = radians(azimuth)
    vertices[:, 0] -= vertices[:, 2] * np.sin(azimuth_rad) / np.tan(elevation_rad)
    vertices[:, 1] -= vertices[:, 2] * np.cos(azimuth_rad) / np.tan(elevation_rad)
    return Polygon(vertices[:, :2]).convex_hull


def turbine_shadow_polygons(item: dict, elevation: float, azimuth: float, ref_lat: float, ref_lon: float) -> tuple[Polygon, Polygon]:
    """Return mast shadow and swept-rotor shadow for one turbine."""
    east, north = latlon_to_local(item["latitude"], item["longitude"], ref_lat, ref_lon)
    mast_tip = np.array([east, north]) + shadow_shift(
        item["mast_height_m"], elevation, azimuth
    )
    mast = LineString([(east, north), tuple(mast_tip)]).buffer(
        item["mast_radius_m"], resolution=6
    )

    angles = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    radius = item["blade_length_m"]
    hub_height = item["mast_height_m"]
    # The north-facing rotor lies in the local East/Up plane.
    rotor_points = []
    for angle in angles:
        point_east = east + radius * np.cos(angle)
        point_height = hub_height + radius * np.sin(angle)
        displacement = shadow_shift(point_height, elevation, azimuth)
        rotor_points.append((point_east + displacement[0], north + displacement[1]))
    return mast, Polygon(rotor_points).convex_hull


def _union_in_batches(polygons: list[Polygon], batch_size: int = 600) -> Polygon | MultiPolygon:
    """Union many polygons without creating one enormous temporary tree."""
    if not polygons:
        return Polygon()
    batches = [unary_union(polygons[start:start + batch_size]) for start in range(0, len(polygons), batch_size)]
    return unary_union(batches)


def soften_envelope_boundary(geometry, interval_minutes: int, flicker: bool = False):
    """Generalize a display boundary into logical straight-line segments.

    The exact union remains available for area calculations.  This display
    geometry removes the small saw-tooth vertices created by discrete solar
    samples, with a tolerance proportional to the selected time interval.
    """
    if geometry.is_empty:
        return geometry
    factor = 0.9 if flicker else 0.45
    tolerance_m = max(0.25, min(14.0, interval_minutes * factor))
    softened = geometry.simplify(tolerance_m, preserve_topology=True)
    return softened if softened.is_valid else softened.buffer(0)


def annual_shadow_envelopes(objects: list[dict], solar_data, reference_latitude: float, reference_longitude: float):
    """Calculate continuous annual solid-shadow and flicker-risk envelopes.

    Consecutive projected polygons are joined by their swept convex envelope.
    This represents the continuous movement between samples without bridging
    across irradiance-filtered periods or night-time gaps.
    """
    relevant = solar_data[
        solar_data["above_ghi_threshold"]
        & (solar_data["ghi"] > 0.0)
        & (solar_data["apparent_elevation"] > 0.0)
    ]
    solid_polygons: list[Polygon] = []
    rotor_polygons: list[Polygon] = []
    if len(solar_data.index) > 1:
        nominal_step_seconds = (
            solar_data.index[1] - solar_data.index[0]
        ).total_seconds()
    else:
        nominal_step_seconds = 0.0

    for item in objects:
        previous_time = None
        previous_solid = None
        previous_rotor = None
        daily_endpoints = {}
        for row in relevant.itertuples():
            elevation = float(row.apparent_elevation)
            azimuth = float(row.azimuth)
            if item["type"] == "Wind turbine":
                current_solid, current_rotor = turbine_shadow_polygons(
                    item, elevation, azimuth, reference_latitude, reference_longitude
                )
            else:
                current_solid = cuboid_shadow_polygon(
                    item, elevation, azimuth, reference_latitude, reference_longitude
                )
                current_rotor = None

            current_time = row.Index
            # Use a longitude-based solar-local date so daylight periods are
            # not split merely because the source timestamps are in UTC.
            solar_day = (
                current_time + timedelta(hours=reference_longitude / 15.0)
            ).date()
            if solar_day not in daily_endpoints:
                daily_endpoints[solar_day] = {
                    "first_solid": current_solid,
                    "last_solid": current_solid,
                    "first_rotor": current_rotor,
                    "last_rotor": current_rotor,
                    "peak_solid": current_solid,
                    "peak_rotor": current_rotor,
                    "peak_elevation": elevation,
                }
            else:
                daily_endpoints[solar_day]["last_solid"] = current_solid
                daily_endpoints[solar_day]["last_rotor"] = current_rotor
                if elevation > daily_endpoints[solar_day]["peak_elevation"]:
                    daily_endpoints[solar_day]["peak_solid"] = current_solid
                    daily_endpoints[solar_day]["peak_rotor"] = current_rotor
                    daily_endpoints[solar_day]["peak_elevation"] = elevation
            contiguous = (
                previous_time is not None
                and nominal_step_seconds > 0
                and (current_time - previous_time).total_seconds()
                <= nominal_step_seconds * 1.05
            )
            if contiguous:
                # The convex hull of two consecutive convex projections is the
                # conservative swept area between them, removing serrated gaps.
                solid_polygons.append(
                    unary_union([previous_solid, current_solid]).convex_hull
                )
                if current_rotor is not None and previous_rotor is not None:
                    rotor_polygons.append(
                        unary_union([previous_rotor, current_rotor]).convex_hull
                    )
            else:
                solid_polygons.append(current_solid)
                if current_rotor is not None:
                    rotor_polygons.append(current_rotor)

            previous_time = current_time
            previous_solid = current_solid
            previous_rotor = current_rotor

        # Join like-for-like daily endpoints. This removes the annual row of
        # daily teeth without ever drawing a bridge from evening to morning.
        ordered_days = sorted(daily_endpoints)
        for previous_day, current_day in zip(ordered_days, ordered_days[1:]):
            if (current_day - previous_day).days != 1:
                continue
            previous = daily_endpoints[previous_day]
            current = daily_endpoints[current_day]
            solid_polygons.append(unary_union([
                previous["first_solid"], current["first_solid"]
            ]).convex_hull)
            solid_polygons.append(unary_union([
                previous["last_solid"], current["last_solid"]
            ]).convex_hull)
            solid_polygons.append(unary_union([
                previous["peak_solid"], current["peak_solid"]
            ]).convex_hull)
            if previous["first_rotor"] is not None and current["first_rotor"] is not None:
                rotor_polygons.append(unary_union([
                    previous["first_rotor"], current["first_rotor"]
                ]).convex_hull)
                rotor_polygons.append(unary_union([
                    previous["last_rotor"], current["last_rotor"]
                ]).convex_hull)
                rotor_polygons.append(unary_union([
                    previous["peak_rotor"], current["peak_rotor"]
                ]).convex_hull)
    solid = _union_in_batches(solid_polygons)
    rotor = _union_in_batches(rotor_polygons)
    flicker_only = rotor.difference(solid) if not rotor.is_empty else rotor
    return solid, flicker_only, len(relevant)


def geometry_to_latlon_rings(geometry, reference_latitude: float, reference_longitude: float):
    """Return exterior and interior rings suitable for Folium GeoJSON."""
    if geometry.is_empty:
        return []
    polygons = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    converted = []
    for polygon in polygons:
        exterior = [local_to_latlon(x, y, reference_latitude, reference_longitude) for x, y in polygon.exterior.coords]
        holes = [
            [local_to_latlon(x, y, reference_latitude, reference_longitude) for x, y in ring.coords]
            for ring in polygon.interiors
        ]
        converted.append((exterior, holes))
    return converted
