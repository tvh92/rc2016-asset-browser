"""Add the health crate's idle effect (rc2016/pipeline/health_effect.py) to an existing extracted .blend.

    python -m rc2016.pipeline.add_health_effect --extracted-root <game folder> --blend in.blend --output out.blend \\
        --blender <blender.exe> [--work <folder for the effect images>]

Reads the crate's sprite images from the archive, writes them as PNGs, and runs Blender in the background on the .blend: the
effect quads are built at the crate (the mesh whose name holds ``Crate_Nanotech``), the images are packed, and the file is
saved under the output name; the input is not changed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--extracted-root', required=True)
    parser.add_argument('--blend', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--blender', required=True)
    parser.add_argument('--work')
    args = parser.parse_args(argv)
    if Path(args.output).resolve() == Path(args.blend).resolve():
        parser.error('--output must differ from --blend')
    sys.path.insert(0, str(REPO))
    from rc2016.levels.scene import open_archive
    from rc2016.pipeline import health_effect
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix='health_effect_'))
    textures = health_effect.prepare_textures(open_archive(args.extracted_root), work)
    job = dict(description=health_effect.effect_description(), textures=textures, output=str(Path(args.output).resolve()))
    job_path = work / 'health_effect_job.json'
    job_path.write_text(json.dumps(job, indent=1), encoding='utf-8')
    command = [args.blender, '-b', str(args.blend), '--python', str(REPO / 'rc2016/pipeline/health_effect.py'), '--', str(job_path)]
    done = subprocess.run(command, capture_output=True, text=True)
    sys.stdout.write(done.stdout[-3000:])
    if done.returncode or 'HEALTH EFFECT' not in done.stdout:
        sys.stderr.write(done.stderr[-3000:])
        return done.returncode or 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
