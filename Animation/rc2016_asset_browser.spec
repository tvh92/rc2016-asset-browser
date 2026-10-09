# PyInstaller one-file build for the portable R&C 2016 Asset Browser and Extractor.
# The exe keeps its old name "PS4 Animation Extractor" until a deliberate rebuild.
# Build from the project root with:
#   C:/Users/Tim/AppData/Local/Programs/Python/Python314/Scripts/pyinstaller.exe \
#       --noconfirm --clean Animation/rc2016_asset_browser.spec

from pathlib import Path
import sys


project_root = Path(SPECPATH).resolve().parent
gui_script = project_root / "Animation" / "rc2016_asset_browser_gui.py"
python_root = Path(sys.prefix)


def data_tree(root: Path, destination: str):
    return [
        (str(path), str(Path(destination) / path.relative_to(root).parent))
        for path in root.rglob("*")
        if path.is_file()
    ]


datas = []
for library, destination in (
    (python_root / "tcl" / "tcl8.6", "lib/tcl8.6"),
    (python_root / "tcl" / "tk8.6", "lib/tk8.6"),
):
    datas.extend(data_tree(library, destination))
datas.extend(data_tree(python_root / "Lib" / "tkinter", "tkinter"))
binaries = [
    (str(python_root / "DLLs" / "tcl86t.dll"), "."),
    (str(python_root / "DLLs" / "tk86t.dll"), "."),
    (str(python_root / "DLLs" / "_tkinter.pyd"), "."),
]


a = Analysis(
    [str(gui_script)],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "_tkinter",
        "tkinter",
        "Animation.animation_audit",
        "Animation.animation_extract",
        "Animation.animation_transforms",
        # The Textures tab decodes BCn and writes PNG through Pillow (imported lazily).
        "PIL.Image",
        "PIL.DdsImagePlugin",
        "PIL.PngImagePlugin",
    ],
    hookspath=[str(project_root / "Animation" / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PS4 Animation Extractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
