# R&C 2016 Asset Browser

A Windows desktop tool for browsing, viewing and exporting the assets of **Ratchet & Clank (PS4, 2016)** straight from the game's own archive.

- **Models:** a searchable tree of all ~7,000 models with an interactive 3D view (Shaded, Textured, Solid, Wireframe), looks/LODs, skeletal animation playback and model details.
- **Textures:** every texture a model uses, or all ~19,000 in the archive, with channel preview and export to the original `.texture`, DDS or PNG.
- **Levels:** loads a `.level` and shows its zones' model instances in a 3D level view. It also runs as a standalone level viewer.
- **Flash UI:** renders the game's Scaleform menus and in-game planet maps, and exports them as PNG.
- **Export:** glTF/GLB (skeleton and clips included) and OBJ without extra software. FBX, USD and `.blend` need Blender. An optional research build produces validated Blender scenes using the game's deferred BRDF.

**No game data is included.** You need your own dump of the game (CUSA01073 or another region), extracted so that the archive folder (`Image0`, with `toc`, `layout.csv` and the archive files) is on disk.

## Requirements

- Windows 10/11 with an OpenGL-capable GPU.
- Python 3.11 or later from python.org, with Tkinter (tested on 3.14). The core viewer uses only the standard library.
- Optional Python packages: `pillow` (Textures tab and PNG export), `numpy` (faster model loading and texture un-tiling), `lz4` (faster archive decompression).
- Optional: Blender 5.0 for FBX/USD/`.blend` export and research builds.

```bash
py -m pip install pillow numpy lz4
```

## Running

From the repository folder:

```bash
py Animation/rc2016_asset_browser_gui.py
```

1. Set **Source** to the extracted game folder and **Output** to an empty folder for exports and the asset index.
2. Click **Scan**. The first scan indexes the archive, which takes a few seconds. Later scans are reused while nothing changes.
3. Pick an asset type (Model, Level or Flash UI) and select an item to view it.
4. **Extract selected** exports the selected models. Formats and Blender's path are under **Options…**.

Viewer controls: left-drag orbits, right-drag pans, the mouse wheel zooms, and double-click resets the view.

The standalone level viewer:

```bash
py Animation/pipeline/Research/AssetBrowserReview/level_viewer.py
```

Use `pythonw` instead of `py` to launch without a console window.

## Repository layout

| Path | Contents |
| --- | --- |
| `Animation/rc2016_asset_browser_gui.py` | Main window, scan/export batch runners, settings |
| `Animation/pipeline/Research/AssetBrowserReview/` | Viewer: 3D viewport and GLSL shading, textures, levels, Flash UI, model export |
| `Animation/pipeline/` | Shared decoders (archive, native model, textures, materials, clips) and the Blender research-build pipeline (`run.py`, `builder.py`) |
| `Animation/pipeline/Research/Levels/` | `.level`/`.zone` decoders |
| `Animation/animation_*.py` | Packed animation decoder and sampler |
| `Claude_shading_fix/` | RC PS4 deferred BRDF for Blender and the captured game-look view |
| `blender_defaults.py`, `shared_lighting.json`, `golden_gate_hills_1k.exr` | Blender scene defaults and the shared review lighting |
| `Animation/pipeline/references/` | Third-party code used by the shader decoder (see below) |

The folder layout and names come from the larger research project this repository was split from. Imports and file paths depend on them, so keep the layout.

More detail:

- [`Animation/pipeline/Research/AssetBrowserReview/README.md`](Animation/pipeline/Research/AssetBrowserReview/README.md): the viewer.
- [`Animation/pipeline/README.md`](Animation/pipeline/README.md): the pipeline.
- [`Animation/pipeline/Research/Levels/README.md`](Animation/pipeline/Research/Levels/README.md): levels.
- [`Claude_shading_fix/README.md`](Claude_shading_fix/README.md): shading.

These READMEs cite research evidence that is not part of this repository.

## Tests

```bash
py -m unittest Animation.test_rc2016_asset_browser_gui Animation.test_gui_batch_isolation Animation.test_gui_pipeline_backend Animation.pipeline.Research.AssetBrowserReview.test_catalog Animation.pipeline.Research.AssetBrowserReview.test_workers Animation.pipeline.Research.AssetBrowserReview.test_viewport Animation.pipeline.Research.AssetBrowserReview.test_texture_preview Animation.pipeline.Research.AssetBrowserReview.test_gfx_render Animation.pipeline.Research.AssetBrowserReview.test_level_view
```

Tests that need game files skip when those files are not present.

## Packaging

`Animation/rc2016_asset_browser.spec` builds a single-file exe with PyInstaller (`pyinstaller --noconfirm --clean Animation/rc2016_asset_browser.spec`, run from the repository root). The exe still runs the pipeline from a checkout of this repository.

## Third-party code

- `Animation/pipeline/references/ALERT/` (the `dat1lib` package only): [ALERT](https://github.com/Tkachov/ALERT) by Tkachov, GPL-3.0. See its `LICENSE.txt`.
- `Animation/pipeline/references/opengnm/src/gcn/gcn_types.h`: opengnm, MIT. See its `LICENSE`.
- `golden_gate_hills_1k.exr`: a downscaled copy of *Golden Gate Hills* from [Poly Haven](https://polyhaven.com/a/golden_gate_hills), CC0.

Ratchet & Clank is a trademark of Sony Interactive Entertainment. This is an unofficial fan tool for studying and preserving the game's assets, and it is not affiliated with Insomniac Games or Sony.
