from __future__ import annotations

from math import cos, radians, sin

import numpy as np


def cuboid_vertices(
    length_x: float,
    width_y: float,
    height_z: float,
    position_x: float = 0.0,
    position_y: float = 0.0,
    ground_elevation: float = 0.0,
    azimuth: float = 90.0,
) -> np.ndarray:
    """Return cuboid vertices in global East, North, Up coordinates.

    The insertion point is the first footprint corner. Local +X follows the
    supplied azimuth clockwise from North; local +Y is 90 degrees clockwise
    from local +X. The cuboid rotates about its insertion corner.
    """
    if min(length_x, width_y, height_z) <= 0:
        raise ValueError("Cuboid dimensions must be greater than zero.")

    angle = radians(azimuth)
    local_x = np.array([sin(angle), cos(angle)])
    local_y = np.array([cos(angle), -sin(angle)])
    origin_xy = np.array([position_x, position_y])

    footprint = np.array(
        [
            origin_xy,
            origin_xy + length_x * local_x,
            origin_xy + length_x * local_x + width_y * local_y,
            origin_xy + width_y * local_y,
        ]
    )
    bottom = np.column_stack((footprint, np.full(4, ground_elevation)))
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
