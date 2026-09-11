"""Annual ground-shadow envelopes on a local East/North plane."""

from __future__ import annotations

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


def annual_shadow_envelopes(objects: list[dict], solar_data, reference_latitude: float, reference_longitude: float):
    """Calculate annual solid-shadow and turbine flicker-risk envelopes."""
    relevant = solar_data[
        solar_data["above_ghi_threshold"] & (solar_data["apparent_elevation"] > 0.5)
    ]
    solid_polygons: list[Polygon] = []
    rotor_polygons: list[Polygon] = []
    for row in relevant.itertuples():
        elevation = float(row.apparent_elevation)
        azimuth = float(row.azimuth)
        for item in objects:
            if item["type"] == "Wind turbine":
                mast, rotor = turbine_shadow_polygons(
                    item, elevation, azimuth, reference_latitude, reference_longitude
                )
                solid_polygons.append(mast)
                rotor_polygons.append(rotor)
            else:
                solid_polygons.append(cuboid_shadow_polygon(
                    item, elevation, azimuth, reference_latitude, reference_longitude
                ))
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
