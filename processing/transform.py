"""Small, dependency-free 3D transform helpers shared by the FBX/BVH pipeline.

All matrices are 4x4 row-major ``list[list[float]]``; all quaternions are
``[x, y, z, w]`` (glTF order). Rotations are right-handed.

Euler convention: FBX Euler order ``"XYZ"`` composes ``R = Rz @ Ry @ Rx`` (X is
applied first). This matches ``processing.retarget._quat_from_euler_xyz`` and is
validated by ``tests/unit/test_fbx.py`` against the real rest T-pose.
"""
from __future__ import annotations

import math

# --------------------------------------------------------------------------- #
# Matrices
# --------------------------------------------------------------------------- #
def identity() -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def translation(x: float, y: float, z: float) -> list[list[float]]:
    m = identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def scaling(x: float, y: float, z: float) -> list[list[float]]:
    m = identity()
    m[0][0], m[1][1], m[2][2] = x, y, z
    return m


def _rot_x(angle: float) -> list[list[float]]:
    c, s = math.cos(angle), math.sin(angle)
    m = identity()
    m[1][1], m[1][2], m[2][1], m[2][2] = c, -s, s, c
    return m


def _rot_y(angle: float) -> list[list[float]]:
    c, s = math.cos(angle), math.sin(angle)
    m = identity()
    m[0][0], m[0][2], m[2][0], m[2][2] = c, s, -s, c
    return m


def _rot_z(angle: float) -> list[list[float]]:
    c, s = math.cos(angle), math.sin(angle)
    m = identity()
    m[0][0], m[0][1], m[1][0], m[1][1] = c, -s, s, c
    return m


_AXIS_ROTATION = {"X": _rot_x, "Y": _rot_y, "Z": _rot_z}


def euler_to_matrix(degrees: list[float] | tuple[float, float, float],
                    order: str = "XYZ") -> list[list[float]]:
    """Euler (degrees) -> rotation matrix. ``order`` lists application order."""
    values = {"X": degrees[0], "Y": degrees[1], "Z": degrees[2]}
    matrix = identity()
    for axis in order:  # last axis listed is applied last -> leftmost factor
        matrix = multiply(_AXIS_ROTATION[axis](math.radians(values[axis])), matrix)
    return matrix


def compose(translation_values: list[float], rotation_degrees: list[float],
            scale_values: list[float], order: str = "XYZ") -> list[list[float]]:
    """T * R * S as used for FBX local transforms without pivots/offsets."""
    return multiply(
        translation(*translation_values[:3]),
        multiply(euler_to_matrix(rotation_degrees, order), scaling(*scale_values[:3])),
    )


def position_of(matrix: list[list[float]]) -> list[float]:
    return [matrix[0][3], matrix[1][3], matrix[2][3]]


def basis_vectors(matrix: list[list[float]]) -> tuple[list[float], list[float], list[float]]:
    """Return the world-space X, Y, Z bone axes (normalised)."""
    def column(i: int) -> list[float]:
        vector = [matrix[0][i], matrix[1][i], matrix[2][i]]
        length = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / length for v in vector]
    return column(0), column(1), column(2)


# --------------------------------------------------------------------------- #
# Quaternions
# --------------------------------------------------------------------------- #
def quat_from_matrix(m: list[list[float]]) -> list[float]:
    """Rotation part of a 4x4 row-major matrix -> normalised quaternion."""
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return normalize_quat([x, y, z, w])


def normalize_quat(q: list[float]) -> list[float]:
    length = math.sqrt(sum(v * v for v in q))
    if length < 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    return [v / length for v in q]


def quat_multiply(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return normalize_quat([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def quat_inverse(q: list[float]) -> list[float]:
    return [-q[0], -q[1], -q[2], q[3]]


def quat_rotate(q: list[float], v: list[float]) -> list[float]:
    x, y, z, w = q
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (y * v[2] - z * v[1])
    ty = 2.0 * (z * v[0] - x * v[2])
    tz = 2.0 * (x * v[1] - y * v[0])
    return [v[0] + w * tx + (y * tz - z * ty),
            v[1] + w * ty + (z * tx - x * tz),
            v[2] + w * tz + (x * ty - y * tx)]


def quat_angle_between(a: list[float], b: list[float]) -> float:
    """Angle in degrees between two orientations."""
    dot = abs(sum(x * y for x, y in zip(normalize_quat(a), normalize_quat(b))))
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot))))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t
