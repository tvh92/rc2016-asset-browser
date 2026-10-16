"""Tk look and runtime: dark theme, tooltips, UI scaling and the bundled Tcl/Tk bootstrap."""
from __future__ import annotations

import os
from pathlib import Path
import sys


_TCL_BOOTSTRAP_DLL = None
_TK_BOOTSTRAP_DLL = None


def windows_prefers_dark() -> bool:
    """True when Windows' 'Choose your app mode' setting is Dark."""

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError):
        return False


TOOLTIP_COLORS = {"background": "#fffbe6", "foreground": "#1f2937"}


def apply_dark_theme(root, ttk) -> None:
    """Dark palette for ttk and classic Tk widgets; call before building widgets."""

    bg, panel, field, fg, muted, accent, border = ("#1e1f22", "#2b2d31", "#26282c", "#e6e6e6",
                                                  "#9aa0a6", "#3d6fb6", "#3c3f44")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
                    lightcolor=panel, darkcolor=bg, troughcolor=panel, insertcolor=fg,
                    selectbackground=accent, selectforeground="#ffffff")
    for widget in ("TFrame", "TLabel", "TCheckbutton", "TLabelframe", "TPanedwindow"):
        style.configure(widget, background=bg, foreground=fg)
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    style.configure("TButton", background=panel, foreground=fg, bordercolor=border, focuscolor=accent)
    style.map("TButton", background=[("disabled", bg), ("pressed", accent), ("active", "#383a40")],
              foreground=[("disabled", "#6b6f75")])
    # Same height as a combobox, for buttons that share a row with one.
    style.configure("Compact.TButton", padding=(8, 1))
    style.map("TCheckbutton", background=[("active", bg)], indicatorbackground=[("selected", accent)])
    # Asset-type selector (segmented Model / Level / Flash UI radio buttons).
    style.configure("Toolbutton", background=panel, foreground=fg, bordercolor=border)
    style.map("Toolbutton", background=[("selected", accent), ("active", "#383a40")],
              foreground=[("selected", "#ffffff")])
    style.configure("TEntry", fieldbackground=field, foreground=fg)
    style.map("TEntry", fieldbackground=[("disabled", bg)], foreground=[("disabled", "#6b6f75")])
    style.configure("TCombobox", fieldbackground=field, background=panel, foreground=fg, arrowcolor=fg)
    style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", bg)],
              foreground=[("disabled", "#6b6f75")], selectbackground=[("readonly", field)],
              selectforeground=[("readonly", fg)])
    style.configure("TMenubutton", background=panel, foreground=fg, arrowcolor=fg, bordercolor=border, relief="flat")
    style.map("TMenubutton", background=[("disabled", bg), ("pressed", "#383a40"), ("active", "#383a40")],
              foreground=[("disabled", "#6b6f75")], arrowcolor=[("disabled", "#6b6f75")])
    style.configure("TSpinbox", fieldbackground=field, background=panel, foreground=fg, arrowcolor=fg)
    style.configure("TNotebook", background=bg, bordercolor=border)
    style.configure("TNotebook.Tab", background=panel, foreground=muted, bordercolor=border)
    style.map("TNotebook.Tab", background=[("selected", bg)], foreground=[("selected", fg)])
    style.configure("Treeview", background=field, fieldbackground=field, foreground=fg, bordercolor=border)
    style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=panel, foreground=fg, bordercolor=border)
    style.map("Treeview.Heading", background=[("active", "#383a40")])
    style.configure("TScrollbar", background=panel, troughcolor=bg, arrowcolor=muted, bordercolor=border)
    # clam's default hover colour for scrollbars is light grey; keep every state dark.
    style.map("TScrollbar", background=[("disabled", bg), ("pressed", accent), ("active", "#4a4d55")],
              arrowcolor=[("disabled", "#4a4d55"), ("pressed", fg), ("active", fg)])
    style.configure("TProgressbar", background=accent, troughcolor=panel)
    # Classic Tk widgets (log, details text, combobox lists, viewport label).
    for pattern, value in (("*Background", bg), ("*Foreground", fg), ("*Text.Background", field),
                           ("*Text.Foreground", fg), ("*Text.insertBackground", fg),
                           ("*selectBackground", accent), ("*selectForeground", "#ffffff"),
                           ("*TCombobox*Listbox.background", field), ("*TCombobox*Listbox.foreground", fg),
                           ("*TCombobox*Listbox.selectBackground", accent),
                           ("*Menu.background", panel), ("*Menu.foreground", fg),
                           ("*Menu.activeBackground", accent), ("*Menu.activeForeground", "#ffffff"),
                           ("*Menu.disabledForeground", "#6b6f75"), ("*Menu.borderWidth", 1),
                           ("*Menu.relief", "flat")):
        root.option_add(pattern, value)
    root.configure(background=bg)
    TOOLTIP_COLORS.update(background="#2b2d31", foreground="#e6e6e6")
    try:
        # Dark title bar (Windows 10 2004+ / 11).
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        pass


class _Tooltip:
    """Small hover help box for a Tk widget."""

    def __init__(self, tk, widget, text: str) -> None:
        self.tk, self.widget, self.text = tk, widget, text
        self.window = None
        self.pending = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.winfo_toplevel().bind("<Unmap>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._hide()
        self.pending = self.widget.after(400, self._show)

    def _show(self) -> None:
        self.pending = None
        self.window = window = self.tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        self.tk.Label(window, text=self.text, justify="left", wraplength=440,
                      background=TOOLTIP_COLORS["background"], foreground=TOOLTIP_COLORS["foreground"], relief="solid",
                      borderwidth=1, padx=8, pady=6).pack()
        # Measure the label, then keep the whole box on screen: flip above the
        # widget when there is no room below, and shift left at the right edge.
        window.update_idletasks()
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if y + height > window.winfo_screenheight():
            y = self.widget.winfo_rooty() - height - 4
        x = max(0, min(x, window.winfo_screenwidth() - width))
        window.wm_geometry(f"+{x}+{max(0, y)}")
        # Leave is not sent when the window moves under a still pointer.
        self.pending = self.widget.after(10000, self._hide)

    def _hide(self, _event=None) -> None:
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window:
            self.window.destroy()
            self.window = None


UI_SCALES = ("75%", "90%", "100%", "110%", "125%", "150%", "175%", "200%")
_BASE_FONT_SIZES: dict[str, int] = {}


def themed_scrolled_text_class(base, tk, ttk):
    """``ScrolledText`` whose scrollbar is a ttk widget: the classic ``tk.Scrollbar`` ignores the dark palette on Windows."""

    class ThemedScrolledText(base):
        def __init__(self, master=None, **kw):
            super().__init__(master, **kw)
            self.vbar.destroy()
            self.vbar = ttk.Scrollbar(self.frame, command=self.yview)
            # Re-pack the text inside its frame so the scrollbar sits to its right. Pack methods
            # on self are forwarded to the outer frame, so call the text widget's own ones.
            tk.Pack.pack_forget(self)
            self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
            tk.Pack.pack_configure(self, side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.configure(yscrollcommand=self.vbar.set)

    return ThemedScrolledText


def apply_ui_scale(root, ttk, percent: int) -> None:
    """Resize every named Tk font (and list rows) to ``percent`` of its default.

    All widgets use Tk's named fonts, which update live, so the window
    re-lays itself out without a restart.
    """

    from tkinter import font as tkfont

    factor = max(50, min(300, percent)) / 100.0
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            named = tkfont.nametofont(name, root=root)
        except Exception:
            continue
        base = _BASE_FONT_SIZES.setdefault(name, int(named.cget("size")) or -12)
        size = round(base * factor)
        named.configure(size=size if size else (1 if base > 0 else -1))
    # Bold named font for the accent "Extract selected" button; follows the default size.
    default = tkfont.nametofont("TkDefaultFont", root=root)
    try:
        accent_font = tkfont.nametofont("AccentButtonFont", root=root)
        accent_font.configure(family=default.cget("family"), size=default.cget("size"), weight="bold")
    except Exception:
        # Keep the Font object alive: tkinter deletes a named font when its Python object is collected.
        root._accent_button_font = tkfont.Font(root=root, name="AccentButtonFont", family=default.cget("family"),
                                               size=default.cget("size"), weight="bold")
    linespace =tkfont.nametofont("TkDefaultFont", root=root).metrics("linespace")
    ttk.Style(root).configure("Treeview", rowheight=int(linespace * 1.35) + 2)

def _configure_tk_runtime() -> None:
    """Point Tk at bundled data and initialize Tcl's library path early.

    Some Python 3.14 Windows builds do not transfer ``TCL_LIBRARY`` into the
    first Tcl interpreter.  Initializing a short-lived interpreter with the
    path set makes the later tkinter-created interpreter inherit the resolved
    Tcl library state.  All failures are left for the normal Tk error message.
    """

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.prefix)))
    if getattr(sys, "frozen", False):
        tcl_library = bundle_root / "lib" / "tcl8.6"
        tk_library = bundle_root / "lib" / "tk8.6"
        tcl_dll = bundle_root / "tcl86t.dll"
        tk_dll = bundle_root / "tk86t.dll"
    else:
        tcl_library = bundle_root / "tcl" / "tcl8.6"
        tk_library = bundle_root / "tcl" / "tk8.6"
        tcl_dll = bundle_root / "DLLs" / "tcl86t.dll"
        tk_dll = bundle_root / "DLLs" / "tk86t.dll"
    # TCL_LIBRARY is not consumed reliably by this Tcl build and a stale
    # inherited value makes Tcl_Init reject an otherwise valid init.tcl.
    os.environ.pop("TCL_LIBRARY", None)
    if tk_library.is_dir():
        os.environ["TK_LIBRARY"] = tk_library.as_posix()
    if not tcl_dll.is_file():
        return
    try:
        import ctypes

        global _TCL_BOOTSTRAP_DLL, _TK_BOOTSTRAP_DLL
        tcl = ctypes.WinDLL(str(tcl_dll))
        # Keep the handle alive until tkinter has created its interpreter.
        _TCL_BOOTSTRAP_DLL = tcl
        # Python 3.14's Tk package index may point at a non-existent ``bin``
        # directory. Loading the bundled Tk DLL first lets Tk_Init resolve the
        # already-loaded module without following that stale relative path.
        _TK_BOOTSTRAP_DLL = ctypes.WinDLL(str(tk_dll))
        tcl.Tcl_FindExecutable.argtypes = [ctypes.c_wchar_p]
        tcl.Tcl_FindExecutable.restype = None
        tcl.Tcl_FindExecutable(None)
        tcl.Tcl_CreateInterp.restype = ctypes.c_void_p
        interpreter = tcl.Tcl_CreateInterp()
        if not interpreter:
            return
        try:
            tcl.Tcl_SetVar.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            tcl.Tcl_SetVar.restype = ctypes.c_char_p
            tcl.Tcl_SetVar(interpreter, b"tcl_library", tcl_library.as_posix().encode("utf-8"), 0)
            tcl.Tcl_Init.argtypes = [ctypes.c_void_p]
            tcl.Tcl_Init.restype = ctypes.c_int
            tcl_init_rc = int(tcl.Tcl_Init(interpreter))
            if tcl_init_rc == 0:
                # Set this only after Tcl_Init. This Python 3.14 build rejects
                # the variable during its first interpreter initialization,
                # but tkinter needs it when it creates its own interpreter.
                os.environ["TCL_LIBRARY"] = tcl_library.as_posix()
        finally:
            tcl.Tcl_DeleteInterp.argtypes = [ctypes.c_void_p]
            tcl.Tcl_DeleteInterp(interpreter)
    except (AttributeError, OSError, TypeError, ValueError):
        return

