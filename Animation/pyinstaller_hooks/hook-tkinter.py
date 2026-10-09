"""Keep the standard Tk modules when the local Tcl installation is unusual.

The Python installation used to build this portable executable reports its
Tcl version as unavailable to PyInstaller's discovery hook, even though the
runtime files are present.  The spec supplies those files explicitly; this
hook keeps the pure-Python tkinter modules in the frozen import graph.
"""

hiddenimports = [
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "tkinter.ttk",
]
