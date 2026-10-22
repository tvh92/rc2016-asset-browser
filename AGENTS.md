# R&C 2016 Asset Browser — agent guide

A Windows desktop tool that reads the Ratchet & Clank (PS4, 2016) game archive directly: an asset browser with an interactive 3D model, level and Flash UI viewer, plus exporters (glTF/GLB/OBJ, FBX/USD/.blend through Blender, and a validated research Blender pipeline). The code is the Python package `rc2016/`. Its history and evidence live in the local-only `Research/` folder inside this checkout. It is gitignored and never committed; the index and sorting rules are in [Research/research.md](Research/research.md).

## Layout

| Path | Contents |
| --- | --- |
| `rc2016/app/` | Entry point (`main.py`), main window (`window.py`), batch runners (`batch.py`), theme and settings, browser UI (`browser.py`), background workers, saved-result catalog |
| `rc2016/viewer/` | OpenGL viewport, GLSL shading, textures and per-graph material rules, animation playback, model loader |
| `rc2016/levels/` | `.level`/`.zone` decoders and the level viewer |
| `rc2016/research/` | Read-only corpus structural hypotheses and [research reports](docs/research.md) |
| `rc2016/flash/` | Scaleform GFX renderer, Flash UI tab, planet-map export |
| `rc2016/export/` | Portable model export and the Blender conversion script |
| `rc2016/formats/` | Archive, asset index, native model, skeleton, texture, material and animation decoders |
| `rc2016/shaders/` | Materialgraph shader analysis: GCN decoder, translation, families, Blender node emission and validation |
| `rc2016/pipeline/` | Research Blender pipeline (`run.py` CLI, `builder.py` in Blender), deferred BRDF (`game_shader.py`), scene defaults, lighting, profiles, per-weapon assemblies (`weapons/`) |
| `assets/` | Shared review lighting (JSON + CC0 HDRI) and the captured game-view tables |
| `third_party/` | ALERT `dat1lib` (GPL-3.0, used for weapon config links) and opengnm `gcn_types.h` (MIT) |
| `tests/`, `docs/`, `packaging/`, `scripts/` | Unit tests, reference docs, PyInstaller spec, PowerShell pipeline wrapper |

Read the doc for the subsystem before changing it: [viewer](docs/viewer.md), [levels](docs/levels.md), [pipeline](docs/pipeline.md), [animation](docs/animation.md), [shading](docs/shading.md). The code is the source of truth when a doc and the code disagree; fix the doc.

## Working rules

- Treat agent usage as limited: reason carefully about the uncertainty that matters, reuse validated mappings and scripts, prefer targeted inspection and deterministic checks over long trial loops, diagnose a failure from its log before retrying, and stop once the result is correct and verified.
- Never add game data to the repository: no extracted assets, `.model`/`.texture`/`.material` files, DDS or PNG exports, renders, `.blend` outputs, reports or logs. The repository holds code, small configuration/profile data, docs and the CC0 HDRI.
- Paths such as `Research/2026-10-22_from-PS4-animation-pipeline/...`, `decodes/...` or `*_DECODE.md` in code comments and recorded evidence strings cite notes inside the local `Research/` folder. They are provenance, not files in git; do not link them from tracked docs. Full layout: [Research/research.md](Research/research.md).
- Never present an inferred shader or lighting value as decoded game data. Keep unknown parameters, runtime tints, time-driven terms and VFX as visible limitations.
- Runtime output goes to the user's output root, caches to `.cache/` (ignored), settings to `%APPDATA%/RC2016 Asset Browser/`. Nothing writes into the source folders.
- Scripts, smoke tests and manual runs must pass an explicit temporary `--output-root` (or `APPDATA`) under the session scratchpad. Never rely on the default output root, and never let a check write into the repository.
- Line endings are per file: `window.py`, `gallery.py` and `docs/viewer.md` are CRLF, most other files are LF. Check with `grep -c $'\r' <file>` before editing and keep the file's existing endings. Never convert a file's line endings. Before committing, `git diff --stat` must show only the lines you meant to change; a whole-file diff means the endings were rewritten.

## Shading and textures

- DDS images use `Alpha Mode: Channel Packed`; alpha is linear data. Colourspace follows the native DXGI format: sRGB for formats 29/72/75/78/91/93/99, everything else Non-Color; normals are always Non-Color.
- `_c` is base colour (its alpha is a self-lit mask, never transparency), `_g` is specular F0 (grey or coloured, per graph writer) with gloss, `_n` is a DirectX tangent normal at strength 1.0 without a green flip.
- Opaque surfaces use the game's deferred BRDF (`rc2016/pipeline/game_shader.py`, the viewer's `rc2016/viewer/shading.py`): diffuse `_c` plus GGX/Schlick specular with F0 = `_g`, F90 = sat(60·F0), roughness 2^(-2.75·gloss), added together; self-lit = `_c.rgb × _c.a × K` with K from material key `0x66EFA55B`. Metallic stays 0. Do not reintroduce `_c × (1 − _g)`, an 18% Principled mix, `1 − gloss` roughness floors or colour tuning.
- Blend and cull come from the `.material` flags word (`rc2016/pipeline/render_state.py`). Glass, energy and VFX forward materials only have the specific decoded rules in the docs.
- New viewer material rules must be mirrored in the Blender front-end (`materials.viewer_graph_rule`, `game_shader.apply_viewer_graph_rule`) and checked by `rc2016/shaders/validation.py`.

## Pipeline

- Keep native Y-up mesh, skeleton and animation values for numerical checks; apply the verified `(x, y, z) -> (x, -z, y)` presentation basis to displayed objects only.
- Build a candidate `.blend`, reopen it in a separate Blender process, validate source hashes, structure, materials, packed images, actions and lighting, then promote atomically. One failed model must not discard the other results.
- Lighting defaults come from `assets/lighting/shared_lighting.json` (review lighting, not game lighting). The captured Veldin game look is opt-in. Missing Cycles or HDRI must fail loudly; never edit global Blender preferences.
- Weapon V1/V5 variants switch with ordinary eye hiding and `hide_render`; never with viewport disable, view-layer exclusion or `hide_viewport`.
- `run.py` hashes the package code into an implementation fingerprint; any code change invalidates cached builds, which is intended.

## Testing

```bash
python -m unittest discover -s tests -t .
```

Run from the repository root. Before committing, also run the lint check (install once with `python -m pip install ruff`):

```bash
python -m ruff check rc2016 tests
```

`ruff.toml` selects the undefined-name rule (F821), which catches a missing import or a name lost in a code splice before anything runs. It must report no errors. Headless GUI smoke tests (Tkinter, background workers) must run under a timeout, must not set the `'source'` setting (it starts a scan that can stall the run), and must use a temporary `APPDATA`. Tests that need game files read the `RC2016_RESEARCH` environment variable (the game archive folder, `CUSA01073`) and skip without it; tests that need saved evidence read `Research/` relative to the checkout and skip when it is absent. For changes to decoding, rendering, export or the pipeline, also check against the real game archive: an archive scan (`python rc2016/pipeline/run.py --extracted-root <game> --output-root <new folder> --blender <blender.exe> --list-models-json <file>`), the affected models in the viewer or exporter, and for pipeline changes a research build that passes reopen validation. Blender-side modules (`builder.py`, `game_shader.py`, weapon merges) only import inside Blender.

## Git and GitHub

- Remote: `https://github.com/tvh92/rc2016-asset-browser` (private), branch `main`. The user leaves git management to agents.
- Check `git status` at the start of a task. After a change passes its tests, commit it as a focused commit with a clear message and push. No force-pushes or history rewrites.
- `.gitattributes` stores files byte for byte (`* -text`); keep it, because `run.py` hashes its own sources.
- Keep docs in step with behaviour: reference style, no dated change logs, no personal paths or machine-specific locations.
