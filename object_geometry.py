from __future__ import annotations

from math import atan2, cos, degrees, pi, radians, sin

import numpy as np


def cuboid_vertices(
    length_x: float,
    width_y: float,
    height_z: float,
    azimuth: float = 90.0,
) -> np.ndarray:
    """Return cuboid vertices on a flat local East, North, Up plane.

    The insertion point is the first footprint corner. Local +X follows the
    supplied azimuth clockwise from North; local +Y completes a right-handed
    East-North-Up system. The cuboid rotates about its insertion corner.
    """
    if min(length_x, width_y, height_z) <= 0:
        raise ValueError("Cuboid dimensions must be greater than zero.")

    angle = radians(azimuth)
    local_x = np.array([sin(angle), cos(angle)])
    local_y = np.array([-cos(angle), sin(angle)])
    origin_xy = np.array([0.0, 0.0])

    footprint = np.array(
        [
            origin_xy,
            origin_xy + length_x * local_x,
            origin_xy + length_x * local_x + width_y * local_y,
            origin_xy + width_y * local_y,
        ]
    )
    bottom = np.column_stack((footprint, np.zeros(4)))
    top = bottom.copy()
    top[:, 2] += height_z
    return np.vstack((bottom, top))


def cuboid_dimension_midpoints(vertices: np.ndarray) -> dict[str, np.ndarray]:
    """Return label positions for the three dimensions."""
    return {
        "x": (vertices[0] + vertices[1]) / 2,
        "y": (vertices[0] + vertices[3]) / 2,
        "z": (vertices[0] + vertices[4]) / 2,
    }


def cuboid_footprint_latlon(
    latitude: float,
    longitude: float,
    length_x: float,
    width_y: float,
    azimuth: float,
) -> list[tuple[float, float]]:
    """Return footprint corners as WGS84 coordinates for map display.

    A local tangent-plane approximation is used, which is appropriate for
    building-scale dimensions. The first coordinate is the insertion corner.
    """
    local_vertices = cuboid_vertices(length_x, width_y, 1.0, azimuth=azimuth)[:4]
    earth_radius = 6_378_137.0
    latitude_radians = radians(latitude)
    coordinates = []
    for east, north, _ in local_vertices:
        latitude_offset = north / earth_radius * 180 / pi
        longitude_offset = east / (earth_radius * cos(latitude_radians)) * 180 / pi
        coordinates.append((latitude + latitude_offset, longitude + longitude_offset))
    return coordinates


def footprint_center(coordinates: list[tuple[float, float]]) -> tuple[float, float]:
    """Return the centre of a small geographic footprint."""
    return (
        sum(point[0] for point in coordinates) / len(coordinates),
        sum(point[1] for point in coordinates) / len(coordinates),
    )


def bearing_from_coordinates(
    origin_latitude: float,
    origin_longitude: float,
    target_latitude: float,
    target_longitude: float,
) -> float:
    """Return initial WGS84 bearing clockwise from North in degrees."""
    origin_latitude_radians = radians(origin_latitude)
    target_latitude_radians = radians(target_latitude)
    longitude_delta = radians(target_longitude - origin_longitude)
    east_component = sin(longitude_delta) * cos(target_latitude_radians)
    north_component = (
        cos(origin_latitude_radians) * sin(target_latitude_radians)
        - sin(origin_latitude_radians)
        * cos(target_latitude_radians)
        * cos(longitude_delta)
    )
    return degrees(atan2(east_component, north_component)) % 360
