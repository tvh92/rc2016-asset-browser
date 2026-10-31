# Corpus research toolkit

`rc2016.research` scans DAT1 assets to find structural candidates for undecoded sections and aligned record fields. It uses the existing archive reader, decompressor and zone section parser. It imports no Qt, OpenGL or Blender modules and changes no decoder or viewer behaviour. See [levels](levels.md) for established layouts.

## Command line

```
python -m rc2016.research <game or asset_archive root> --out <output folder>
                         [--ext .zone .level .zone_static]
                         [--level novalis] [--limit N] [--model-features]
```

The default extensions are `.level`, `.zone` and `.zone_static`. `--level` selects a level directory, including its zones and static siblings. `--limit` selects the first N assets in canonical path/id order. Both filters are recorded in the report; a filtered scan is not whole-corpus evidence.

The output folder must be outside the repository and source root. It receives `report.json` and `report.md`. The existing archive index cache is redirected under `<out>/.cache/`; decompressed asset bytes remain in memory and are released per file. No game bytes, reports or logs belong in the repository. Archive access verifies repeated occurrences using the existing reader. The archive remains read-only.

`--model-features` additionally reads referenced native model tables and used material references, without expanding geometry or loading a renderer. This can increase I/O substantially. It correlates any-mesh flag bits and source materialgraph paths. Metadata failures are recorded, and missing metadata is excluded from the corresponding population. Graph paths do not establish shader behaviour.

`--loose` reads standalone DAT1 files, primarily synthetic fixtures. It is explicit and does not replace archive access. File order, examples and output keys are deterministic; timings and timestamps are not embedded in the report.

## Modules

| Module | Role |
| --- | --- |
| `source.py` | Archive streaming, bounds checks, existing DAT1 section parser, known decoder coverage |
| `census.py` | Size statistics, GCD and candidate strides, parallel counts, string/section offset tests, heap range coverage |
| `fields.py` | Aligned 2/4/8-byte candidates, value/bit counts, floats, indexes, offsets, string hashes, vector and matrix evidence |
| `correlate.py` | Bit/feature contingency counts, phi and mutual information; optional native metadata |
| `hypothesis.py` | Executable claims over fixed-stride tables or offset-selected records |
| `cross_reference.py` | Actual vendored dat1lib reader execution and CRC algorithms; guarded generic serialized reader |
| `report.py` | Two-pass analysis and deterministic JSON/Markdown |
| `mip_layout.py` | Texture payload sizes against top-mip-only, tile-padded and unpadded mip-chain layouts (`python -m rc2016.research.mip_layout --source-root <Image0>`); a prerequisite for uploading stored mips instead of generating them |
| `forward_programs.py` | Resource-table texture and sampler usage of a graph's forward pixel programs (`forward_resource_usage`), read from the GCN descriptor loads; results in [Ratchet forward passes](material-decoding/ratchet-forward-passes.md) |
| `npc_audit.py` | Per-model animation and material coverage for one path prefix (default `character/npc/`); see [Character audit](#character-audit) |
| `inheritance_audit.py` | Bit-0 scale-compensation rules: inverse-bind fit of every model and world-basis shear of paired clips per rule (`python -m rc2016.research.inheritance_audit <game or asset_archive root> --out <folder>`); see [pipeline](pipeline.md#inheritance-rule) |

The census ranks undecoded sections before decoded ones, with requested open items first. Within each group, score is file count times total section bytes. Heap coverage uses existing instance/decal reference ranges; unclaimed bytes can include alignment padding. The heap is only partially decoded.

A candidate stride is a divisor of all observed section sizes, optionally supported by a sibling dat1lib reader or equal element counts. Divisibility does not establish record boundaries. Automatic field analysis selects one explicit stride hypothesis per eligible undecoded table: a stride matching an established pointer-table count across at least two files first, then a compatible dat1lib stride, otherwise the largest enumerated common divisor up to 512 bytes. The material-pair section uses the documented 8-byte candidate. Other divisors remain visible in the census.

Instance fields use actual offsets into the heap and the existing size field. Each aligned slot uses only records containing that slot. Optional tails are not filled with zeros for statistical purposes. Probes stop at 512 bytes, with longer-record and absent-slot counts visible. Distinct-value tracking retains at most 256 values; when exceeded, the count is a lower bound. Displayed distributions may be incomplete. Record examples and counterexamples contain file paths and absolute byte offsets.

Float candidates require finite values with magnitude at most 10 million, excluding nonzero magnitudes below `1e-6`. Unit float3/float4 windows and affine/orthonormal matrix predicates supplement scalar candidates. Small integers can also satisfy index or offset predicates; competing candidates stay visible. CRC matches use ALERT's actual CRC32 and CRC64 routines, normalized asset-index paths and known printable strings. Matching an algorithm is evidence of structure, not field meaning or unique identity.

## Character audit

```
python -m rc2016.research.npc_audit <game or asset_archive root> --out <folder> [--prefix character/npc/]
```

Writes `audit.json` and `audit.md` to `--out` (outside the repository and source root). It reads archive bytes in memory, so no decode cache is written. Per model under the prefix:

- **Animation.** The animsets the game pairs with the model (the pipeline's actor-reference walk) with their named clips, each clip's coverage of the model joints, and a suggested plain idle (a clip name containing `idle` without a variant word such as fidget, aim, hover or turn; a hint, not a rule). Every real-time cutscene shot of the model (`animation_sources.cinematic_sources`) is decoded and scored: `motion_score` is the mean per-frame world displacement of all joints, root included, and the lowest decoded absolute shot is `calmest_cutscene_shot`. `same_rig_models` lists other `character/` models whose rest rig `animation_sources.rig_comparison` finds the same (covering at least 90% of the joints), with the animsets the game pairs with them that `--borrow-animset` could offer.
- **Materials.** For every material the model references: graphs, the matched viewer `GRAPH_RULES` prefix, family preflight, native translation status and its first blockers, blend state, fur-shell flag, and the front-end the pipeline builds (`materials.final_shader_status`, the same decision as `prepare_materials`). `category` ranks the evidence: `native_translation`, `exact_viewer_rule`, `profile_recipe`, `prefix_viewer_rule` (a family prefix such as `basic_skin/`), `partial_family`, `generic_default_reading` (no rule: coloured `_g` and C-alpha self-lit by default), `forward_partial`, `forward_approximation`, `colour_only`, `missing_base_colour`, `unset`, `failed`.

## dat1lib and serialized sections

ALERT is vendored under `third_party/ALERT` and is GPL-3.0. Zone, level and zone-static section classes supply class names, comment names and sibling-game hypotheses. For each matching class, the cross-check actually constructs it on section bytes, catches exceptions and records status counts and representative failures.

Statuses are `parses_cleanly`, `raises`, `leftover_bytes` and `opaque_reader`. A constructor that completes does not prove complete consumption. Fixed strides declared in reader code expose trailing bytes; unmeasured consumption is counted separately. A reader with no parsed attributes is opaque. No opaque reader is counted as a structural decode.

For serialized tags, including `0x81999057` and `0x2300D240`, the toolkit directly calls a guarded subclass of dat1lib's generic `SerializedSection`. It deliberately avoids the older-engine zone subclass bypass. The generic reader is tested under two explicitly named version hypotheses, 201800 and 202300; neither is claimed to identify this game version. Guards bound lengths, counts, nesting, node reads and string-name offsets. Unknown node types raise rather than silently becoming null. Skipped object payload is counted as leftover bytes. The adapter validates string terminators and reads UTF-8 strings using the vendored wire layout; this adaptation is explicit because the generic modern reader assumes ASCII. Name pointers must land on printable string-pool starts. The report contains only the resulting schema's key paths and types, never primitive values. JSON retains the schema; Markdown shows at most 1,000 paths per reader/version and states when truncated.

## Hypothesis API and CLI

```python
from rc2016.research.hypothesis import Claim, RecordSource, run

source = RecordSource(section=0x06ABCAB2, offsets_section=0x6987F172)
claims = [Claim('candidate model index', 0x58, 'index', target=0xC6A5905E)]
result = run(corpus, source, claims, examples=8)
```

`RecordSource` accepts a fixed `stride` with `offsets_section=None`, or a u32 offsets section pointing into the selected section. Offset sources may use a record `size_field`; without one, adjacent offsets delimit records. `Claim` supports `index`, `equals_count`, `range` and `float3_bounds`. Scalar types are `u16`, `u32`, `u64` and `f32`. Target counts require a declared `target_stride` (default 4). `float3_bounds` compares three floats with the current zone's instance-translation bounds. A missing target or missing field fails visibly; malformed record sources are reported separately.

```
python -m rc2016.research hypothesis <archive root> --out <folder>
    --claim '{"name":"candidate index","offset":96,"kind":"index","dtype":"u16","target":305419896}'
```

Repeat `--claim` for additional JSON claim objects. Use `--section`, `--stride`, `--offsets-section` and `--size-field` to select another record source. This command writes `hypotheses.json`, containing pass/fail counts and the first `--counterexamples N` failures with file, record offset, field offset and observed value. The normal report also checks the established model-index and minimum-size invariants; placement correlations additionally require `decode_zone` to pass.

## Report schema and interpretation

The stable schema identifier is `rc2016-research-v1`.

| Key | Contents |
| --- | --- |
| `corpus` | Selection, scanned/selected counts, extensions, archive namespaces, decoded-content fingerprint, repeat verification, scan errors |
| `confidence` | Thresholds and the distinction between structural confidence and meaning |
| `sections` | Ranked tags, coverage, size statistics, candidate strides, parallel counts, pointer matches, dat1lib metadata, examples |
| `cross_checks` | Per-reader/version status counts, leftover bytes, unmeasured consumption, schemas and representative executions |
| `heap` | Referenced/unclaimed byte totals, gap-size distribution, examples and errors |
| `layouts` | Explicit record-source hypothesis, coverage, aligned fields, competing candidates and structural predicates |
| `instance_summary` | Placement-validated record count, namespace coverage and observed record/tail sizes |
| `correlations` | Feature populations, contingency cells, phi/MI, number of comparisons and strongest associations per bit |
| `hypotheses` | Established claims' pass/fail counts, source errors and counterexamples |
| `model_features` | Whether enrichment ran, readable/tested models and unresolved model/material metadata |
| `analysis_errors`, `limits` | Incomplete analysis and explicit probe/example/distinct-value limits |

Every field candidate includes matches, tests, confidence and counterexamples. High confidence requires a specific byte predicate with at least 20 tests and 99% agreement, or an exact observed constant. Medium requires at least five tests and 90% agreement for a specific predicate. Divisibility-only evidence, generic floats/integers and correlations remain low confidence. These labels never establish semantic meaning. Parallel-count and matrix predicates state their own counts; look at the counterexamples before promoting a candidate.

The default scan includes every matching archive entry, including legacy assets outside `levels/`. Instance layouts and correlation pools keep those namespaces separate. Start with the main `levels` namespace, instance flags and tails, heap coverage, material-pair and spawn-record layouts, then the dat1lib cross-check and serialized schemas. A raising reader is a useful incompatibility result. A successful reader with unmeasured consumption is weaker than one with explicit byte coverage. Correlations are exploratory and uncorrected for multiple comparisons; repeated placements are not independent samples. The toolkit still lists `0x032D450A` as an open item; it and the instance-tail material-override block are now decoded, see [per-instance material overrides](levels.md#per-instance-material-overrides) and [actor material swaps](levels.md#actor-material-swaps-not-drawn).

## Tests

```
python -m unittest discover -s tests -t .
```

`tests/test_research.py` builds synthetic DAT1 and archive fixtures, covering stride/count inference, field candidates, CRC matches, planted correlations, hypotheses, throwing readers, leftovers, actual generic serialized parsing, deterministic CLI reports and headless imports. The optional real-archive smoke test reads `RC2016_RESEARCH` and skips without it. Full corpus validation uses the CLI and its recorded coverage, not synthetic test success.
