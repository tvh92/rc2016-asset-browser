"""Source CPU arithmetic for an explicitly supplied effective instance matrix.

This helper does not select a runtime flag, assemble a CB1 record, or assert
that a serialized zone matrix is the draw's effective matrix. Callers must
establish those inputs separately. Vector magnitudes are preserved.
"""
import math
import struct


DETERMINANT_THRESHOLD = 2. ** -20


def _f32(value):
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except (TypeError, ValueError, OverflowError, struct.error) as exc:
        raise ValueError('Instance matrix arithmetic exceeds finite float32') from exc
    if not math.isfinite(result):
        raise ValueError('Instance matrix arithmetic requires finite float32')
    return result


def instance_vector_matrix(matrix, *, copy_linear):
    """Return three padded axis vectors written by the source CPU helper.

    ``matrix`` is 16 native-basis floats: groups at 0/4/8 are the local axes,
    group 12 is translation. ``copy_linear`` must be an explicitly supplied
    bool corresponding to the CPU helper selector; it is never inferred from
    GPU record flags or a material name.

    The general branch forms the inverse transpose of the column-stacked
    linear axes, using the source float32 operation order. If abs(det)<2^-20,
    its reciprocal factor is 1 and the unscaled cofactors are retained.
    The fast branch copies xyz unchanged. All three output w values are zero.
    Finite arithmetic only; no normalization or singular-matrix identity.
    """
    if type(copy_linear) is not bool:
        raise ValueError('CPU instance-matrix selector must be explicitly supplied')
    try:
        values = tuple(matrix)
    except TypeError as exc:
        raise ValueError('Instance matrix requires 16 finite floats') from exc
    if len(values) != 16:
        raise ValueError('Instance matrix requires 16 finite floats')
    values = tuple(_f32(v) for v in values)
    a, b, c = (values[p:p+3] for p in (0, 4, 8))
    if copy_linear:
        return tuple(v for axis in (a, b, c) for v in (*axis, 0.))
    mul = lambda x, y: _f32(x*y)
    add = lambda x, y: _f32(x+y)
    sub = lambda x, y: _f32(x-y)
    # Exact multiply/add/subtract sequence at native helper 0x00E88D83..DD7.
    det = add(mul(mul(a[1], b[2]), c[0]), mul(mul(a[0], b[1]), c[2]))
    det = add(mul(mul(a[2], b[0]), c[1]), det)
    det = sub(det, mul(mul(a[0], b[2]), c[1]))
    det = sub(det, mul(mul(a[1], b[0]), c[2]))
    det = sub(det, mul(mul(a[2], b[1]), c[0]))
    reciprocal = _f32(1./det) if abs(det) >= DETERMINANT_THRESHOLD else 1.
    def cross(u, v):
        return (sub(mul(u[1], v[2]), mul(u[2], v[1])),
                sub(mul(u[2], v[0]), mul(u[0], v[2])),
                sub(mul(u[0], v[1]), mul(u[1], v[0])))
    return tuple(v for axis in (cross(b, c), cross(c, a), cross(a, b))
                 for v in (*(mul(v, reciprocal) for v in axis), 0.))
