"""python -m rc2016.research <archive root> --out <folder>."""
import argparse
import json
from pathlib import Path
import sys

from .source import Corpus, DEFAULT_EXTENSIONS


def integer(value):
    return int(value, 0)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    hypothesis = bool(argv and argv[0] == 'hypothesis')
    if hypothesis:
        argv.pop(0)
    parser = argparse.ArgumentParser(description='Read-only structural hypotheses over level/zone DAT1 assets')
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ext', nargs='+', default=DEFAULT_EXTENSIONS)
    parser.add_argument('--level')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--loose', action='store_true', help='Read standalone DAT1 fixtures instead of an archive')
    parser.add_argument('--model-features', action='store_true', help='Read referenced model/material metadata for correlations')
    if hypothesis:
        parser.add_argument('--section', type=integer, default=0x06ABCAB2)
        parser.add_argument('--stride', type=integer)
        parser.add_argument('--offsets-section', type=integer)
        parser.add_argument('--size-field', type=integer)
        parser.add_argument('--claim', action='append', required=True, help='JSON object with Claim constructor fields')
        parser.add_argument('--counterexamples', type=int, default=8)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    out, root = args.out.resolve(), args.root.resolve()
    if out.is_relative_to(repo) or out.is_relative_to(root):
        parser.error('--out must be outside the repository and source root')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    corpus = Corpus(root, out, args.ext, args.level, args.limit, args.loose)
    with corpus.opened():
        if hypothesis:
            from .hypothesis import Claim, RecordSource, run
            from rc2016.common import dump
            source = RecordSource(args.section, args.stride,
                                  args.offsets_section if args.stride else (args.offsets_section or 0x6987F172),
                                  args.size_field if args.size_field is not None else (None if args.stride else 0x44))
            claims = [Claim(**json.loads(value)) for value in args.claim]
            result = run(corpus, source, claims, args.counterexamples)
            result['scan_errors'] = corpus.errors
            dump(out / 'hypotheses.json', result)
            print(f"Hypothesis report: {out / 'hypotheses.json'}")
        else:
            from .report import build, write
            def progress(stage, done, total):
                print(f'{stage}: {done}/{total} files', file=sys.stderr, flush=True)
            result = build(corpus, args.model_features, progress)
            write(result, out)
            print(f"Report: {out / 'report.md'}")
            print(f"JSON: {out / 'report.json'}")
            if corpus.errors or result['analysis_errors']:
                return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
