"""Annual ground-shadow envelopes on a local East/North plane."""

from __future__ import annotations

from math import cos, pi, radians

import numpy as np
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon
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
    point_east = east + radius * np.cos(angles)
    point_height = hub_height + radius * np.sin(angles)
    elevation_rad = radians(elevation)
    azimuth_rad = radians(azimuth)
    horizontal_per_vertical = 1.0 / np.tan(elevation_rad)
    rotor_points = np.column_stack((
        point_east - point_height * np.sin(azimuth_rad) * horizontal_per_vertical,
        north - point_height * np.cos(azimuth_rad) * horizontal_per_vertical,
    ))
    return mast, Polygon(rotor_points).convex_hull


def _union_in_batches(polygons: list[Polygon], batch_size: int = 600) -> Polygon | MultiPolygon:
    """Union many polygons without creating one enormous temporary tree."""
    if not polygons:
        return Polygon()
    batches = [unary_union(polygons[start:start + batch_size]) for start in range(0, len(polygons), batch_size)]
    return unary_union(batches)


def _swept_hull(first: Polygon, second: Polygon) -> Polygon:
    """Return the convex swept envelope without performing an expensive union."""
    return GeometryCollection((first, second)).convex_hull


def soften_envelope_boundary(geometry, interval_minutes: int, flicker: bool = False):
    """Generalize a display boundary into logical straight-line segments.

    The exact union remains available for area calculations.  This display
    geometry removes insignificant sub-metre vertices after the azimuth-extreme
    envelope is built. The same tolerance is used for every study time step so
    the displayed restriction boundary remains consistent.
    """
    if geometry.is_empty:
        return geometry
    tolerance_m = 4.0 if flicker else 2.0
    softened = geometry.simplify(tolerance_m, preserve_topology=True)
    return softened if softened.is_valid else softened.buffer(0)


def annual_shadow_envelopes(
    objects: list[dict], solar_data, reference_latitude: float,
    reference_longitude: float, boundary_solar_data=None,
    return_individual: bool = False,
):
    """Calculate annual envelopes from geometry-defining solar extremes.

    Every timestamp is filtered for sun position and irradiance, but only the
    minimum and maximum solar elevations in narrow azimuth sectors can define
    the external envelope. Interior, redundant projections are skipped.
    """
    relevant = solar_data[
        solar_data["above_ghi_threshold"]
        & (solar_data["ghi"] > 0.0)
        & (solar_data["apparent_elevation"] > 0.0)
    ]
    boundary_source = solar_data if boundary_solar_data is None else boundary_solar_data
    boundary_relevant = boundary_source[
        boundary_source["above_ghi_threshold"]
        & (boundary_source["ghi"] > 0.0)
        & (boundary_source["apparent_elevation"] > 0.0)
    ]
    solid_polygons: list[Polygon] = []
    rotor_polygons: list[Polygon] = []
    individual_results = []
    azimuth_bin_degrees = 0.25
    envelope_rows = boundary_relevant[["apparent_elevation", "azimuth"]].copy()
    envelope_rows["azimuth_bin"] = np.floor(
        envelope_rows["azimuth"] / azimuth_bin_degrees
    ).astype(int)
    grouped_rows = list(envelope_rows.groupby("azimuth_bin", sort=True))

    for item in objects:
        object_solid_polygons: list[Polygon] = []
        object_rotor_polygons: list[Polygon] = []

        def keep_solid(polygon):
            solid_polygons.append(polygon)
            object_solid_polygons.append(polygon)

        def keep_rotor(polygon):
            rotor_polygons.append(polygon)
            object_rotor_polygons.append(polygon)

        def project(row):
            elevation = float(row.apparent_elevation)
            azimuth = float(row.azimuth)
            if item["type"] == "Wind turbine":
                return turbine_shadow_polygons(
                    item, elevation, azimuth, reference_latitude, reference_longitude
                )
            return (
                cuboid_shadow_polygon(
                    item, elevation, azimuth, reference_latitude, reference_longitude
                ),
                None,
            )

        previous_bin = None
        previous_solid_section = None
        previous_rotor_section = None
        first_section = None
        last_section = None

        for azimuth_bin, rows in grouped_rows:
            low_row = rows.loc[rows["apparent_elevation"].idxmin()]
            high_row = rows.loc[rows["apparent_elevation"].idxmax()]
            low_solid, low_rotor = project(low_row)
            high_solid, high_rotor = project(high_row)
            solid_section = _swept_hull(low_solid, high_solid)
            rotor_section = (
                _swept_hull(low_rotor, high_rotor)
                if low_rotor is not None and high_rotor is not None else None
            )
            keep_solid(solid_section)
            if rotor_section is not None:
                keep_rotor(rotor_section)

            if previous_bin is not None and azimuth_bin - previous_bin <= 1:
                keep_solid(_swept_hull(previous_solid_section, solid_section))
                if previous_rotor_section is not None and rotor_section is not None:
                    keep_rotor(_swept_hull(previous_rotor_section, rotor_section))

            if first_section is None:
                first_section = (azimuth_bin, solid_section, rotor_section)
            last_section = (azimuth_bin, solid_section, rotor_section)
            previous_bin = azimuth_bin
            previous_solid_section = solid_section
            previous_rotor_section = rotor_section

        # Join across North only when occupied bins are genuinely adjacent
        # around the 0°/360° boundary (relevant for polar-day locations).
        total_bins = round(360.0 / azimuth_bin_degrees)
        if (
            first_section is not None and last_section is not None
            and first_section[0] + total_bins - last_section[0] <= 1
        ):
            keep_solid(_swept_hull(last_section[1], first_section[1]))
            if last_section[2] is not None and first_section[2] is not None:
                keep_rotor(_swept_hull(last_section[2], first_section[2]))

        object_solid = _union_in_batches(object_solid_polygons)
        object_rotor = _union_in_batches(object_rotor_polygons)
        object_flicker = (
            object_rotor.difference(object_solid)
            if not object_rotor.is_empty else object_rotor
        )
        individual_results.append({
            "id": item.get("id", ""),
            "name": item.get("name", item.get("type", "Object")),
            "type": item.get("type", "Object"),
            "solid": object_solid,
            "flicker": object_flicker,
        })
    solid = _union_in_batches(solid_polygons)
    rotor = _union_in_batches(rotor_polygons)
    flicker_only = rotor.difference(solid) if not rotor.is_empty else rotor
    if return_individual:
        return solid, flicker_only, len(relevant), individual_results
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
