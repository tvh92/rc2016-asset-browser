"""Exact half-open NLA intervals; mixed source rates use a common timeline clock."""
from fractions import Fraction
import math


def nla_sample_bounds(start, frames, sample_step=1):
    """Return (action_end, strip_end) that exposes exactly the integer samples.

    Blender's NLA strip end is evaluated as a boundary, while a source action's
    last key is an inclusive sample.  Mapping an N-sample action to ``N + 1``
    and an N-frame strip therefore makes the first frame of the next adjacent
    strip evaluate the wrong action endpoint.  Keep the established half-frame
    guard for a one-sample action so it remains evaluable without inventing a
    second source sample.
    """
    n = int(frames)
    step = int(sample_step)
    if n < 1:
        raise ValueError('Empty clip cannot occupy an NLA strip')
    if step < 1:
        raise ValueError('NLA sample step must be positive')
    action_end = max(float(n), 1.5)
    strip_end = float(start) + (action_end - 1.0) * step
    return action_end, strip_end


def timeline(entries):
    rates=[Fraction(str(e['fps'])).limit_denominator(1000000) for e in entries]
    if not rates: return [],30
    common=Fraction(math.lcm(*(r.numerator for r in rates)), math.gcd(*(r.denominator for r in rates)))
    if not 1 <= common <= 32767:
        raise ValueError('Source rates require an unsupported Blender overview FPS; no timing resampling was applied')
    cursor, result=1,[]
    for i,(entry,rate) in enumerate(zip(entries,rates)):
        step=common/rate
        if step.denominator != 1: raise ValueError('Nonintegral sample clock')
        n=int(entry['frames']); stride=int(step)
        if n < 1: raise ValueError('Empty clip cannot occupy a timeline strip')
        end=cursor+n*stride
        if end > 1048574: raise ValueError('Timeline exceeds Blender frame range')
        action_end, strip_end = nla_sample_bounds(cursor, n, stride)
        result.append(dict(index=i,action=entry['action'],name=entry['name'],kind=entry['kind'],
            start=cursor,end_exclusive=end,end_inclusive=end-1,frames=n,source_fps=float(rate),sample_step=stride,
            action_start=1,action_end_exclusive=n+1,
            nla_action_end=action_end,nla_end=strip_end))
        cursor=end
    return result,float(common)
