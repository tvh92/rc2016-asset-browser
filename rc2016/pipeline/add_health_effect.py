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
    parser.add_argument('--no-bloom', action='store_true', help='leave out the health crate bloom (compositor nodes)')
    parser.add_argument('--game-tonemap', action=argparse.BooleanOptionalAction, default=True,
                        help="the game's captured exposure and tone curve as compositor nodes (default), with the bloom added after the curve as the game does; --no-game-tonemap keeps the file's view transform")
    parser.add_argument('--keep-file-exposure', action='store_true', help="add the file's own exposure stops (its HDRI review lighting) on top of the game's exposure multiplier; the default is the game's multiplier alone")
    parser.add_argument('--no-flares', action='store_true', help='leave out the anamorphic flares')
    parser.add_argument('--no-light', action='store_true', help='leave out the effect light')
    parser.add_argument('--no-pack-environment', action='store_true', help='keep the shared HDRI as an external file')
    args = parser.parse_args(argv)
    if Path(args.output).resolve() == Path(args.blend).resolve():
        parser.error('--output must differ from --blend')
    sys.path.insert(0, str(REPO))
    from rc2016.levels.scene import open_archive
    from rc2016.pipeline import health_effect
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix='health_effect_'))
    textures = health_effect.prepare_textures(open_archive(args.extracted_root), work)
    job = dict(description=health_effect.effect_description(flares=not args.no_flares, light=not args.no_light), textures=textures, output=str(Path(args.output).resolve()),
               bloom=not args.no_bloom, game_tonemap=args.game_tonemap, keep_file_exposure=args.keep_file_exposure, pack_environment=not args.no_pack_environment,
               repo_hdri=str(REPO / 'assets/lighting/golden_gate_hills_1k.exr'))
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
