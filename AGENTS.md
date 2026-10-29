# R&C 2016 Asset Browser — agent guide

A Windows desktop tool that reads the Ratchet & Clank (PS4, 2016) game archive directly: an asset browser with an interactive 3D model, level and Flash UI viewer, plus exporters (glTF/GLB/OBJ, FBX/USD/.blend through Blender, and a validated research Blender pipeline). The code is the Python package `rc2016/`. Its history and evidence live in the local-only `Research/` folder inside this checkout; it is gitignored and never committed (index and sorting rules: [Research/research.md](Research/research.md)).

## Layout

| Path | Contents |
| --- | --- |
| `rc2016/app/` | Entry point, main window, batch runners, theme and settings, browser UI, background workers, saved-result catalog |
| `rc2016/viewer/` | OpenGL viewport, GLSL shading, textures and per-graph material rules, animation playback, model loader |
| `rc2016/levels/` | `.level`/`.zone` decoders and the level viewer |
| `rc2016/research/` | Read-only corpus structural hypotheses and [research reports](docs/research.md) |
| `rc2016/flash/` | Scaleform GFX renderer, Flash UI tab, planet-map export |
| `rc2016/export/` | Portable model export and the Blender conversion script |
| `rc2016/formats/` | Archive, index, model, skeleton, texture, material and animation decoders |
| `rc2016/shaders/` | Materialgraph shader analysis: GCN decoder, translation, families, Blender node emission and validation |
| `rc2016/pipeline/` | Research Blender pipeline (`run.py` CLI, `builder.py` in Blender), deferred BRDF (`game_shader.py`), scene defaults, lighting, profiles, weapon profile data (`weapons/`) |
| `assets/` | Shared review lighting (JSON + CC0 HDRI) and the captured game-view tables |
| `third_party/` | ALERT `dat1lib` (GPL-3.0, weapon config links) and opengnm `gcn_types.h` (MIT) |

Read the doc for the subsystem before changing it: [viewer](docs/viewer.md), [levels](docs/levels.md), [pipeline](docs/pipeline.md), [animation](docs/animation.md), [shading](docs/shading.md). The code is the source of truth when a doc and the code disagree; fix the doc.

## Working rules

- Diagnose a failure from its log before retrying; prefer targeted inspection and deterministic checks over long trial loops, and reuse validated mappings and scripts.
- Never add game data to the repository: no extracted assets, `.model`/`.texture`/`.material` files, DDS or PNG exports, renders, `.blend` outputs, reports or logs. It holds code, small configuration/profile data, docs and the CC0 HDRI.
- Nothing writes into the repository or the source folders. Runtime output goes to the user's output root, caches to `.cache/` (ignored), settings to `%APPDATA%/RC2016 Asset Browser/`. Scripts, smoke tests and manual runs must pass an explicit temporary `--output-root` (or `APPDATA`) under the session scratchpad, never the default.
- Paths such as `Research/.../`, `decodes/...` or `*_DECODE.md` in code comments and evidence strings cite notes in the local `Research/` folder. They are provenance, not files in git; do not link them from tracked docs.
- Never present an inferred shader or lighting value as decoded game data. Keep unknown parameters, runtime tints, time-driven terms and VFX as visible limitations.
- Line endings are per file and many tracked files are CRLF (most of `rc2016/app/`, much of `levels/`, `formats/materials.py`, `shaders/validation.py`, many tests). Check with Python (count `b'\r\n'` against lone `b'\n'`; `grep -c $'\r'` is unreliable here), keep the file's ending, and write with `newline=''` or in binary mode. Never insert `\r` with `sed`. Before committing, `git diff --stat` must show only the lines you meant to change; a whole-file diff means the endings were rewritten.

## Shading and textures

Equations, slot meanings and graph families are in [shading](docs/shading.md); these are the traps.

- Texture colourspace follows the native DXGI format, not the filename; normals are always Non-Color; alpha is linear data.
- `_c` alpha is a self-lit mask, never transparency. `_n` is a DirectX tangent normal at strength 1.0 without a green flip.
- Opaque surfaces use the game's deferred BRDF (`rc2016/pipeline/game_shader.py`, the viewer's `rc2016/viewer/shading.py`) with metallic 0. Do not reintroduce `_c × (1 − _g)`, an 18% Principled mix, `1 − gloss` roughness floors or colour tuning.
- Blend and cull come from the `.material` flags word (`rc2016/pipeline/render_state.py`). Glass, energy and VFX forward materials only have the specific decoded rules in the docs.
- New viewer material rules must be mirrored in the Blender front-end (`materials.viewer_graph_rule`, `game_shader.apply_viewer_graph_rule`) and checked by `rc2016/shaders/validation.py`.

## Pipeline

Details and CLI options are in [pipeline](docs/pipeline.md); these are the traps.

- Keep native Y-up values for numerical checks; apply the presentation basis to displayed objects only.
- Build a candidate `.blend`, reopen and validate it in a separate Blender process, then promote atomically. One failed model must not discard the other results.
- Lighting defaults are review lighting, not game lighting; the captured Veldin look is opt-in. Missing Cycles or HDRI must fail loudly; never edit global Blender preferences.
- Any code change invalidates cached builds (`run.py` fingerprints the package); that is intended.

## Testing

Run from the repository root; both must pass before committing (`python -m pip install ruff` once):

```bash
python -m unittest discover -s tests -t .
python -m ruff check rc2016 tests
```

- Headless GUI smoke tests (Tkinter, background workers) run under a timeout, must not set the `'source'` setting (it starts a scan that can stall the run), and use a temporary `APPDATA`.
- Tests that need game files read `RC2016_RESEARCH` (the game archive folder, `CUSA01073`) and skip without it; tests that need saved evidence read `Research/` and skip when it is absent.
- For changes to decoding, rendering, export or the pipeline, also check against the real archive: a scan (`python rc2016/pipeline/run.py --extracted-root <game> --output-root <new folder> --blender <blender.exe> --list-models-json <file>`), the affected models in the viewer or exporter, and for pipeline changes a research build that passes reopen validation. Blender-side modules (`builder.py`, `game_shader.py`, weapon merges) only import inside Blender.

## Git and GitHub

- Remote: `https://github.com/tvh92/rc2016-asset-browser` (private), branch `main`. The user leaves git management to agents. No force-pushes or history rewrites.
- Check `git status` at the start of a task. Several sessions can share this checkout, so staged and modified files are often not yours: commit only your own paths (`git commit -o <paths>`, or a temporary `GIT_INDEX_FILE` when a file mixes your hunks with another session's), never a plain `git commit -a`. After a change passes its tests, commit it as a focused commit with a clear message and push.
- `.gitattributes` stores files byte for byte (`* -text`); keep it, because `run.py` hashes its own sources.
- Keep docs in step with behaviour: reference style, no dated change logs, no personal paths or machine-specific locations.
