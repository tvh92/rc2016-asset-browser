"""Tk look and runtime: light and dark themes, tooltips, UI scaling and the bundled Tcl/Tk bootstrap."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import weakref


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

# Named colours of both themes. bg: window background; panel: raised controls; field: text fields and list
# bodies; muted: secondary text; accent*: primary action and selection; hover/scroll_hover/arrow_disabled and
# the bevel pair: ttk control states; canvas/viewport/tile*/focus_ring/placeholder: drawn previews and
# thumbnails; sash: pane dividers; ok/error/running: status text.
PALETTES = {
    "dark": {
        "bg": "#1e1f22", "panel": "#2b2d31", "field": "#26282c", "fg": "#e6e6e6", "muted": "#9aa0a6",
        "accent": "#3d6fb6", "border": "#3c3f44", "select_fg": "#ffffff",
        # Greyed-out text and controls: dim, but clearly readable on the dark background.
        "disabled_fg": "#7b8089", "accent_active": "#2f5a97", "accent_disabled": "#8ea6c9",
        "hover": "#383a40", "scroll_hover": "#4a4d55", "arrow_disabled": "#4a4d55",
        "bevel_light": "#2b2d31", "bevel_dark": "#1e1f22",
        "tooltip_bg": "#2b2d31", "tooltip_fg": "#e6e6e6",
        "canvas": "#2b2b2b", "viewport": "#24262b", "tile": "#3a3a3a", "tile_text": "#777777",
        "focus_ring": "#8ab4e8", "placeholder": "#2b2d31", "sash": "#2b2d31",
        "ok": "#34d399", "error": "#f87171", "running": "#60a5fa",
    },
    "light": {
        "bg": "#f0f0f0", "panel": "#e1e1e1", "field": "#ffffff", "fg": "#1f2937", "muted": "#6b7280",
        "accent": "#2563eb", "border": "#c8c8c8", "select_fg": "#ffffff",
        "disabled_fg": "#9ca3af", "accent_active": "#1d4ed8", "accent_disabled": "#c7d2fe",
        "hover": "#d4d7dc", "scroll_hover": "#9ca3af", "arrow_disabled": "#d1d5db",
        "bevel_light": "#ffffff", "bevel_dark": "#c8c8c8",
        "tooltip_bg": "#fffbe6", "tooltip_fg": "#1f2937",
        "canvas": "#d9dce1", "viewport": "#c9cdd3", "tile": "#c3c7ce", "tile_text": "#6b7280",
        "focus_ring": "#93c5fd", "placeholder": "#e5e7eb", "sash": "#c8c8c8",
        "ok": "#047857", "error": "#b91c1c", "running": "#1d4ed8",
    },
}
# Light until apply_theme runs, which is how an unthemed window looks.
_current = {"name": "light"}
# (weak reference to a classic Tk widget, {Tk option: palette token}), reconfigured on every theme switch.
_tracked: list = []
# Weak references to callbacks that redraw things a widget option cannot restyle (canvas items, text tags).
_listeners: list = []


def current_palette() -> dict[str, str]:
    return PALETTES[_current["name"]]


def color(token: str) -> str:
    return PALETTES[_current["name"]][token]


def is_dark() -> bool:
    return _current["name"] == "dark"


def gl_color(token: str) -> tuple[float, float, float, float]:
    """A palette colour as an opaque OpenGL RGBA tuple."""

    value = color(token).lstrip("#")
    return (*(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)), 1.0)


def _configure_widget(widget, options: dict[str, str], palette: dict[str, str]) -> bool:
    """Apply ``{option: token}`` to a widget; False when the widget has been destroyed."""

    try:
        widget.configure(**{option: palette[token] for option, token in options.items()})
        return True
    except Exception:
        try:
            exists = bool(widget.winfo_exists())
        except Exception:
            exists = False
        if exists:
            raise   # a wrong option or token on a live widget is a bug, not a destroyed window
        return False


def track(widget, **options: str):
    """Keep a classic Tk widget in the theme: ``track(text, background="field", foreground="fg")``.

    Classic widgets ignore ttk styles and later ``option_add`` calls, so each is restyled on every switch.
    Only a weak reference is kept.
    """

    _tracked.append((weakref.ref(widget), dict(options)))
    _configure_widget(widget, options, current_palette())
    return widget


def listen(callback) -> None:
    """Call ``callback()`` after each theme switch (a bound method is held weakly)."""

    _listeners.append(weakref.WeakMethod(callback) if hasattr(callback, "__self__") else (lambda: callback))


def apply_titlebar(window, dark: bool | None = None) -> None:
    """Dark or light title bar for a Tk window (Windows 10 2004+ / 11); follows the current theme by default."""

    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if (is_dark() if dark is None else dark) else 0)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
        # A visible window keeps its old title bar until its frame is recalculated.
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x37)   # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
        # DWM reads the attribute only when the caption is next painted, and a frame change does not repaint it.
        # Toggling the activation state (WM_NCACTIVATE) does, and ends on the window's real state.
        active = ctypes.windll.user32.GetForegroundWindow() == hwnd
        for state in (not active, active):
            ctypes.windll.user32.SendMessageW(hwnd, 0x0086, int(state), 0)
    except (AttributeError, OSError):
        pass


def _configure_ttk(style, p: dict[str, str]) -> None:
    bg, panel, field, fg, muted, accent, border = (p[key] for key in
                                                  ("bg", "panel", "field", "fg", "muted", "accent", "border"))
    disabled, hover, select_fg = p["disabled_fg"], p["hover"], p["select_fg"]
    style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
                    lightcolor=p["bevel_light"], darkcolor=p["bevel_dark"], troughcolor=panel, insertcolor=fg,
                    selectbackground=accent, selectforeground=select_fg)
    for widget in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TLabelframe", "TPanedwindow"):
        style.configure(widget, background=bg, foreground=fg)
    style.configure("TLabelframe.Label", background=bg, foreground=fg)
    # Secondary and greyed-out text; a label's own foreground option would not follow a theme switch.
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Disabled.TLabel", foreground=disabled)
    style.configure("TButton", background=panel, foreground=fg, bordercolor=border, focuscolor=accent)
    style.map("TButton", background=[("disabled", bg), ("pressed", accent), ("active", hover)],
              foreground=[("disabled", disabled)])
    # Same height as a combobox, for buttons that share a row with one.
    style.configure("Compact.TButton", padding=(8, 1))
    # Checkboxes draw their own box (see _install_check_indicator); radio buttons keep the clam indicator.
    style.map("TCheckbutton", background=[("active", bg)], foreground=[("disabled", disabled)])
    style.configure("TRadiobutton", indicatorbackground=field, indicatorforeground=select_fg, bordercolor=muted)
    style.map("TRadiobutton", background=[("active", bg)], foreground=[("disabled", disabled)],
              indicatorbackground=[("disabled", bg), ("selected", accent)],
              indicatorforeground=[("disabled", disabled)], bordercolor=[("disabled", disabled)])
    # Asset-type selector (segmented Model / Level / Flash UI radio buttons).
    style.configure("Toolbutton", background=panel, foreground=fg, bordercolor=border)
    style.map("Toolbutton", background=[("selected", accent), ("active", hover)],
              foreground=[("selected", select_fg)])
    style.configure("TEntry", fieldbackground=field, foreground=fg)
    style.map("TEntry", fieldbackground=[("disabled", bg)], foreground=[("disabled", disabled)])
    style.configure("TCombobox", fieldbackground=field, background=panel, foreground=fg, arrowcolor=fg)
    style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", bg)],
              foreground=[("disabled", disabled)], selectbackground=[("readonly", field)],
              selectforeground=[("readonly", fg)])
    style.configure("TMenubutton", background=panel, foreground=fg, arrowcolor=fg, bordercolor=border, relief="flat")
    style.map("TMenubutton", background=[("disabled", bg), ("pressed", hover), ("active", hover)],
              foreground=[("disabled", disabled)], arrowcolor=[("disabled", disabled)])
    style.configure("TSpinbox", fieldbackground=field, background=panel, foreground=fg, arrowcolor=fg)
    style.configure("TNotebook", background=bg, bordercolor=border)
    style.configure("TNotebook.Tab", background=panel, foreground=muted, bordercolor=border)
    style.map("TNotebook.Tab", background=[("selected", bg)], foreground=[("selected", fg)])
    style.configure("Treeview", background=field, fieldbackground=field, foreground=fg, bordercolor=border)
    style.map("Treeview", background=[("selected", accent)], foreground=[("selected", select_fg)])
    style.configure("Treeview.Heading", background=panel, foreground=fg, bordercolor=border)
    style.map("Treeview.Heading", background=[("active", hover)])
    style.configure("TScrollbar", background=panel, troughcolor=bg, arrowcolor=muted, bordercolor=border)
    # clam's default hover colour for scrollbars is light grey; keep every state in the palette.
    style.map("TScrollbar", background=[("disabled", bg), ("pressed", accent), ("active", p["scroll_hover"])],
              arrowcolor=[("disabled", p["arrow_disabled"]), ("pressed", fg), ("active", fg)])
    style.configure("TProgressbar", background=accent, troughcolor=panel)


CHECK_SIZE = 18   # pixels; clam's own indicator is about 13 and its mark too small to read on the dark field
CHECK_GAP = 6     # transparent pixels between the box and the label
CHECK_ELEMENT = "RC.Checkbutton.indicator"
_CHECK_IMAGES = ("off", "off_hover", "on", "on_hover", "disabled_off", "disabled_on")


def _near_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float, radius: float = 1.1) -> bool:
    dx, dy = x2 - x1, y2 - y1
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return (px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2 <= radius * radius


def _draw_check_box(image, fill: str, border: str, mark: str | None) -> None:
    """A square box: a 1-pixel border, the fill, and a two-pixel check mark when ``mark`` is given."""

    segments = ((4.5, 9.0, 7.5, 12.0), (7.5, 12.0, 13.5, 6.0))
    last = CHECK_SIZE - 1
    image.blank()
    for y in range(CHECK_SIZE):
        for x in range(CHECK_SIZE):
            if x in (0, last) or y in (0, last):
                colour = border
            elif mark and any(_near_segment(x + 0.5, y + 0.5, *segment) for segment in segments):
                colour = mark
            else:
                colour = fill
            image.put(colour, to=(x, y, x + 1, y + 1))


def _with_indicator(layout, element: str):
    """The layout spec with its checkbutton indicator replaced by ``element``."""

    out = []
    for name, options in layout:
        options = dict(options)
        if "children" in options:
            options["children"] = _with_indicator(options["children"], element)
        out.append((element if name == "Checkbutton.indicator" else name, options))
    return out


def _install_check_indicator(root, style, p: dict[str, str]) -> None:
    """Checkboxes get a drawn box: the images are made once per window and redrawn on every theme switch."""

    import tkinter
    images = getattr(root, "_check_images", None)
    if images is None:
        images = root._check_images = {name: tkinter.PhotoImage(master=root, width=CHECK_SIZE + CHECK_GAP, height=CHECK_SIZE)
                                       for name in _CHECK_IMAGES}
        # The first matching state wins, so the more specific states come first.
        style.element_create(CHECK_ELEMENT, "image", images["off"],
                             (("disabled", "selected"), images["disabled_on"]),
                             (("disabled",), images["disabled_off"]),
                             (("selected", "active"), images["on_hover"]),
                             (("selected",), images["on"]),
                             (("active",), images["off_hover"]))
        style.layout("TCheckbutton", _with_indicator(style.layout("TCheckbutton"), CHECK_ELEMENT))
    field, bg, accent = p["field"], p["bg"], p["accent"]
    _draw_check_box(images["off"], field, p["muted"], None)
    _draw_check_box(images["off_hover"], field, accent, None)
    _draw_check_box(images["on"], accent, accent, p["select_fg"])
    _draw_check_box(images["on_hover"], p["accent_active"], p["accent_active"], p["select_fg"])
    _draw_check_box(images["disabled_off"], bg, p["disabled_fg"], None)
    _draw_check_box(images["disabled_on"], p["disabled_fg"], p["disabled_fg"], bg)


def _apply_option_defaults(root, p: dict[str, str]) -> None:
    """Defaults for classic Tk widgets created from now on (log, details text, combobox lists, menus)."""

    bg, panel, field, fg, accent, select_fg = (p[key] for key in ("bg", "panel", "field", "fg", "accent", "select_fg"))
    for pattern, value in (("*Background", bg), ("*Foreground", fg), ("*highlightBackground", bg),
                           ("*Text.Background", field), ("*Text.Foreground", fg), ("*Text.insertBackground", fg),
                           ("*selectBackground", accent), ("*selectForeground", select_fg),
                           ("*Listbox.background", field), ("*Listbox.foreground", fg),
                           ("*TCombobox*Listbox.background", field), ("*TCombobox*Listbox.foreground", fg),
                           ("*TCombobox*Listbox.selectBackground", accent),
                           ("*Menu.background", panel), ("*Menu.foreground", fg),
                           ("*Menu.activeBackground", accent), ("*Menu.activeForeground", select_fg),
                           ("*Menu.disabledForeground", p["disabled_fg"]), ("*Menu.borderWidth", 1),
                           ("*Menu.relief", "flat")):
        root.option_add(pattern, value)


def _descendants(widget):
    for child in list(widget.winfo_children()):
        yield child
        yield from _descendants(child)


def _retheme_existing(root, p: dict[str, str]) -> None:
    """Windows and combobox drop-downs that already exist; the option database only reaches new ones."""

    import tkinter
    for widget in _descendants(root):
        kind = widget.winfo_class()
        try:
            if kind == "Toplevel":
                widget.configure(background=p["bg"])
                if not widget.wm_overrideredirect():
                    apply_titlebar(widget)
            elif kind == "TCombobox":
                popdown = root.tk.call("ttk::combobox::PopdownWindow", str(widget))
                root.tk.call(f"{popdown}.f.l", "configure", "-background", p["field"], "-foreground", p["fg"],
                             "-selectbackground", p["accent"], "-selectforeground", p["select_fg"])
        except tkinter.TclError:
            pass   # destroyed meanwhile


def apply_theme(root, ttk, dark: bool) -> None:
    """Dark or light palette for ttk and classic Tk widgets. Safe to call again to switch a running window."""

    _current["name"] = "dark" if dark else "light"
    p = current_palette()
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    _configure_ttk(style, p)
    _install_check_indicator(root, style, p)
    _apply_option_defaults(root, p)
    root.configure(background=p["bg"])
    TOOLTIP_COLORS.update(background=p["tooltip_bg"], foreground=p["tooltip_fg"])
    apply_titlebar(root)
    _retheme_existing(root, p)
    _tracked[:] = [(ref, options) for ref, options in _tracked
                   if (widget := ref()) is not None and _configure_widget(widget, options, p)]
    live = []
    for ref in _listeners:
        callback = ref()
        if callback is None:
            continue
        live.append(ref)
        try:
            callback()
        except Exception:   # one stale redraw must not leave the rest of the window half-switched
            import traceback
            traceback.print_exc()
    _listeners[:] = live


def apply_dark_theme(root, ttk) -> None:
    """Dark palette; kept for callers that predate ``apply_theme``."""

    apply_theme(root, ttk, True)


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
            track(self, background="field", foreground="fg", insertbackground="fg", selectbackground="accent",
                  selectforeground="select_fg", highlightbackground="bg")
            track(self.frame, background="bg")

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

