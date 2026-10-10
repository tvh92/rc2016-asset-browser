# R&C 2016 Asset Browser

A Windows desktop tool for browsing, viewing and exporting the assets of **Ratchet & Clank (PS4, 2016)** straight from the game's own archive.

- **Models:** a searchable tree of all ~7,000 models with an interactive 3D view (Shaded, Textured, Solid, Wireframe), looks/LODs, skeletal animation playback and model details.
- **Gallery:** a thumbnail grid of a folder or search result (rendered by a separate process and cached in the output folder); click a tile to open the model.
- **Textures:** every texture a model uses, or all ~19,000 in the archive, with channel preview and export to the original `.texture`, DDS or PNG.
- **Levels:** loads a `.level` and shows its zones' model instances in a 3D level view. It also runs as a standalone level viewer.
- **Flash UI:** renders the game's Scaleform menus and in-game planet maps, and exports them as PNG.
- **Export:** glTF/GLB (skeleton and clips included) and OBJ with no extra software. FBX, USD and `.blend` need Blender. An optional research pipeline builds validated Blender scenes using the game's deferred BRDF.

**No game data is included.** You need your own dump of the game (CUSA01073 or another region), extracted so that the archive folder (`Image0`, with `toc`, `layout.csv` and the archive files) is on disk.

## Requirements

- Windows 10/11 with an OpenGL-capable GPU.
- Python 3.11 or later from python.org, with Tkinter (tested on 3.14). The core viewer uses only the standard library.
- Optional Python packages: `pillow` (Textures tab and PNG export), `numpy` (faster model loading and texture un-tiling), `lz4` (faster archive decompression).
- Optional: Blender 5.0 for FBX/USD/`.blend` export and the research pipeline.

```bash
py -m pip install pillow numpy lz4
```

## Getting the code

```bash
git clone https://github.com/tvh92/rc2016-asset-browser.git C:/rc2016-asset-browser
```

Clone into a short path. Some file names are 64-character hashes, so a deep folder can exceed Windows' 260-character path limit.

## Running

From the repository folder:

```bash
py -m rc2016
```

1. Set **Source** to the extracted game folder and **Output** to an empty folder for exports and the asset index.
2. Click **Scan**. The first scan indexes the archive, which takes a few seconds. Later scans are reused while nothing changes.
3. Pick an asset type (Model, Level or Flash UI) and select an item to view it.
4. **Extract selected** exports the selected models. Formats and Blender's path are under **Options…**.

Viewer controls: left-drag orbits, right-drag pans, the mouse wheel zooms, and double-click resets the view.

Other entry points:

```bash
py -m rc2016.levels.viewer
```

```bash
py -m rc2016.flash.planet_maps "<extracted game folder>" "<output folder>"
```

```bash
py rc2016/pipeline/run.py --help
```

Use `pyw` instead of `py` to launch the windows without a console. Settings are saved in `%APPDATA%\RC2016 Asset Browser`.

## Repository layout

| Path | Contents |
| --- | --- |
| `rc2016/app/` | Main window, browser UI, scan and export batch runners |
| `rc2016/viewer/` | 3D viewport, GLSL shading, textures and material rules, animation playback |
| `rc2016/levels/` | Level viewer and the `.level`/`.zone` decoders |
| `rc2016/flash/` | Flash UI renderer and planet-map export |
| `rc2016/export/` | glTF/GLB/OBJ export and Blender conversion |
| `rc2016/formats/` | Decoders for the archive, asset index, models, skeletons, textures, materials and animation |
| `rc2016/shaders/` | Materialgraph shader analysis (GCN decoding, translation, shader families) |
| `rc2016/pipeline/` | Research Blender pipeline, the game's deferred BRDF for Blender, scene defaults, per-weapon assemblies |
| `assets/` | Shared review lighting (settings and HDRI) and the captured game-view tables |
| `docs/` | Reference documentation |
| `tests/` | Unit tests |
| `third_party/` | Third-party code (see below) |
| `packaging/`, `scripts/` | PyInstaller spec and the PowerShell pipeline wrapper |

Documentation:

- [Viewer and browser](docs/viewer.md)
- [Levels](docs/levels.md)
- [Research pipeline](docs/pipeline.md)
- [Animation format](docs/animation.md)
- [Shading and lighting](docs/shading.md)

## Tests

```bash
py -m unittest discover -s tests -t .
```

Tests that need game files skip unless the `RC2016_RESEARCH` environment variable points at a folder that has them.

## Packaging

`packaging/asset_browser.spec` builds a single-file exe with PyInstaller (`pyinstaller --noconfirm --clean packaging/asset_browser.spec`, run from the repository root). The exe runs the pipeline from the repository checkout it sits in.

## License

This project is licensed under the GNU General Public License v3.0; see [`LICENSE`](LICENSE). It is GPL because it includes and runs ALERT's GPL-3.0 `dat1lib`.

## Third-party code and assets

- `third_party/ALERT/` (the `dat1lib` package only): [ALERT](https://github.com/Tkachov/ALERT) by Tkachov, GPL-3.0. See its `LICENSE.txt`. Used to read weapon config files for the research pipeline's V1/V5 weapon assemblies, and by `rc2016/research/` for section-reader cross-checks and CRC hashing.
- `third_party/opengnm/gcn_types.h`: opengnm, MIT. See its `LICENSE`. Provides the GCN opcode tables for the shader decoder.
- `assets/lighting/golden_gate_hills_1k.exr`: a downscaled copy of *Golden Gate Hills* from [Poly Haven](https://polyhaven.com/a/golden_gate_hills), CC0.

Ratchet & Clank is a trademark of Sony Interactive Entertainment. This is an unofficial fan tool for studying and preserving the game's assets, and it is not affiliated with Insomniac Games or Sony.
