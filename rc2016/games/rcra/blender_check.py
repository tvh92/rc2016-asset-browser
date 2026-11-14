"""Check emitted Blender materials against the translated recipe, texel by texel.

    python -m rc2016.games.rcra.blender_check --game <install> --blender <blender.exe> --output-root <folder> <material path> ...

For each material: the Blender entry (rc2016.games.rcra.blender_export) is built, rc2016/export/rcra_blender_check.py
bakes every surface output of the emitted node tree on a UV-unit plane in Cycles, and the result is compared with the
recipe evaluated by rc2016.shaders.sm5 for the same inputs and the same unit textures (bilinear repeat on both sides).
Prints and returns the worst absolute difference per surface term.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from rc2016.games.rcra import blender_export, surface as rsurface

CHECK_SCRIPT = Path(__file__).resolve().parents[2] / 'export' / 'rcra_blender_check.py'
TERMS = {'albedo': ('albedo.r', 'albedo.g', 'albedo.b'), 'f0': ('f0.r', 'f0.g', 'f0.b'),
         'scalars': ('gloss', 'occlusion', 'emission')}


def load_unit(row):
    if 'slices' in row:
        return [load_unit(r) for r in row['slices']]
    if row['kind'] == 'float':
        return np.fromfile(row['path'], dtype='<f4').reshape(row['height'], row['width'], 4)
    from PIL import Image
    data = np.asarray(Image.open(row['path']).convert('RGBA'), np.float32) / 255.0
    if row['colourspace'] == 'sRGB':   # the sampler returns linear rgb
        rgb = data[..., :3]
        data[..., :3] = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return data


def bilinear(image, u, v):
    """Bilinear repeat lookup at game (u, v), texel centres at (k + 0.5) / size, as Blender's 'Linear'."""
    h, w = image.shape[:2]
    x, y = u * w - 0.5, v * h - 0.5
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = x - x0, y - y0
    rows, cols = [y0 % h, (y0 + 1) % h], [x0 % w, (x0 + 1) % w]
    top = image[rows[0], cols[0]] * (1 - fx) + image[rows[0], cols[1]] * fx
    bottom = image[rows[1], cols[0]] * (1 - fx) + image[rows[1], cols[1]] * fx
    return [float(c) for c in top * (1 - fy) + bottom * fy]


def window(entry, size):
    """(origin, span) of the baked UV window: each bake pixel covers at most a quarter of the largest texture's texel, so
    the bake's sub-pixel jitter (Cycles' minimum filter width) moves a lookup by a negligible fraction of a texel."""
    widths = [r.get('width', 1) for row in entry['images'].values() for r in row.get('slices', [row])]
    span = min(1.0, size / (4.0 * max(widths + [1])))
    return 0.3, span


def expected(entry, size):
    """{term: (size*size, 3)} from the recipe, pixel rows from Blender v = 0, plane normal +Z, tangent +X."""
    units = {b: load_unit(row) for b, row in entry['images'].items()}

    def sample(node, args):
        image = units[node['binding']]
        if isinstance(image, list):
            image = image[int(min(max(math.floor(args[2] + 0.5), 0), len(image) - 1))]
        return bilinear(image, args[0], args[1])
    out = defaultdict(list)
    origin, span = window(entry, size)
    for j in range(size):
        for i in range(size):
            ub, vb = origin + span * (i + 0.5) / size, origin + span * (j + 0.5) / size
            inputs = {'TEXCOORD.x': ub, 'TEXCOORD.y': 1.0 - vb, 'TEXCOORD2.x': 0.0, 'TEXCOORD2.y': 1.0, 'TEXCOORD2.z': 0.0,
                      'TEXCOORD3.x': 1.0, 'TEXCOORD3.y': 0.0, 'TEXCOORD3.z': 0.0, 'TEXCOORD3.w': 1.0,
                      'TEXCOORD4.x': 1.0, 'TEXCOORD4.y': 1.0, 'TEXCOORD4.z': 1.0, 'TEXCOORD4.w': 1.0, 'SV_IsFrontFace': True}
            terms = rsurface.evaluate_recipe(entry['recipe'], inputs, sample)
            for name, keys in TERMS.items():
                out[name].append([terms[k] for k in keys])
            n = np.array([terms['normal.x'], -terms['normal.z'], terms['normal.y']])   # game -> Blender
            out['normal'].append(list(n / max(np.linalg.norm(n), 1e-12)))
            out['alpha'].append([0.0 if terms['clip'] else 1.0] * 3)
    return {k: np.array(v) for k, v in out.items()}


def bake(entry, blender, size, folder):
    job = Path(folder) / 'job.json'
    out = Path(folder) / 'baked.json'
    origin, span = window(entry, size)
    job.write_text(json.dumps(dict(recipe=entry['recipe'], images=entry['images'], size=size, uv_origin=origin,
                                   uv_span=span, out=str(out))), encoding='utf-8')
    env = dict(os.environ, BLENDER_USER_RESOURCES=str(Path(folder) / 'user'),
               PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    run = subprocess.run([str(blender), '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                          str(CHECK_SCRIPT), '--', '--job', str(job)], capture_output=True, text=True, env=env,
                         encoding='utf-8', errors='replace', timeout=900)
    if run.returncode or not out.is_file():
        raise RuntimeError('Blender check failed:\n' + '\n'.join((run.stdout + run.stderr).splitlines()[-25:]))
    return {k: np.array(v) for k, v in json.loads(out.read_text(encoding='utf-8')).items()}


def check(source, material_path, blender, folder, size=37):
    """{term: worst absolute difference} of one material; raises when it has no Blender entry."""
    entry = blender_export.material_entry(source, material_path, Path(folder) / 'units')
    if 'error' in entry:
        raise rsurface.Unsupported(entry['error'])
    want = expected(entry, size)
    got = bake(entry, blender, size, folder)
    report = {}
    for name, values in want.items():
        diff = np.abs(got[name][:, :3] - values)
        report[name] = dict(max=float(diff.max()), p99=float(np.percentile(diff.max(axis=1), 99)))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--game', required=True)
    parser.add_argument('--blender', required=True)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--size', type=int, default=37)
    parser.add_argument('materials', nargs='+')
    args = parser.parse_args(argv)
    from rc2016.games.rcra.source import RcraSource
    source = RcraSource(args.game, args.output_root)
    results = {}
    for path in args.materials:
        with tempfile.TemporaryDirectory(dir=args.output_root) as tmp:
            try:
                results[path] = check(source, path, args.blender, tmp, args.size)
            except (rsurface.Unsupported, RuntimeError) as error:
                results[path] = dict(error=str(error))
        print(path, json.dumps(results[path]), flush=True)
    return results


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
