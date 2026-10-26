#!/usr/bin/env python3
"""Icon Studio — layers, prompt gen, pixel editor, multi-size export."""
from __future__ import annotations

import copy
import json
import math
import os
import random
import re
import sys
import tempfile
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageGrab, ImageTk
except ImportError:
    raise SystemExit("Install Pillow: pip install -r requirements.txt")

from icon_gen import detect_prompt_kind, generate_icon_from_prompt


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Project file or PyInstaller bundle (_MEIPASS)."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / name
        if bundled.is_file():
            return bundled
    p = app_root() / name
    if p.is_file():
        return p
    return app_root() / name


def _logo_to_ico_path() -> Path | None:
    """Stable .ico next to app (or temp) built from logo.jpg / bundled app_icon.ico."""
    bundled = resource_path("app_icon.ico")
    if bundled.is_file():
        return bundled
    logo = resource_path("logo.jpg")
    if not logo.is_file():
        return None
    dest = app_root() / "app_icon.ico"
    try:
        if dest.is_file() and dest.stat().st_mtime >= logo.stat().st_mtime:
            return dest
    except OSError:
        pass
    try:
        im = Image.open(logo).convert("RGBA")
    except OSError:
        return None
    sizes = (16, 24, 32, 48, 64, 128, 256)
    frames = [im.resize((s, s), Image.Resampling.LANCZOS) for s in sizes]
    target = dest
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            target,
            format="ICO",
            sizes=[(f.width, f.height) for f in frames],
            append_images=frames[1:],
        )
        return target
    except OSError:
        target = Path(tempfile.gettempdir()) / "icon_studio_app_icon.ico"
        try:
            frames[0].save(
                target,
                format="ICO",
                sizes=[(f.width, f.height) for f in frames],
                append_images=frames[1:],
            )
            return target
        except OSError:
            return None


def apply_window_icon(win: tk.Misc, holder: tk.Misc | None = None) -> None:
    """Title bar + taskbar icon from logo.jpg (via .ico)."""
    keep = holder if holder is not None else win
    ico_path = _logo_to_ico_path()
    logo = resource_path("logo.jpg")
    photos: list[ImageTk.PhotoImage] = []
    if logo.is_file():
        try:
            im = Image.open(logo).convert("RGBA")
            for side in (16, 32, 48):
                photos.append(ImageTk.PhotoImage(im.resize((side, side), Image.Resampling.LANCZOS)))
        except (OSError, tk.TclError):
            photos.clear()
    if ico_path is not None:
        try:
            win.iconbitmap(str(ico_path))
        except tk.TclError:
            pass
    if photos:
        try:
            win.iconphoto(True, *photos)
            keep._app_icon_photos = photos  # type: ignore[attr-defined]
        except tk.TclError:
            pass


ROOT = app_root()
READY = ROOT / "ready_icons"
READY.mkdir(parents=True, exist_ok=True)

COMP_SIZE = 128
CELL16 = 14
CELL11 = 16
CELL84 = 4   # 84×48 full LCD → 336×192 preview
CELL72 = 5   # 72×28 → 360×140 preview
CELL22 = 8   # 22×26 → 176×208 preview
CELL6 = 22   # 6×7 small icon
COMP_VIEW = 256  # composition display size (compose still COMP_SIZE)
CUSTOM_MAX = 128  # manual WxH clamp


def cell_for_size(w: int, h: int) -> int:
    """Pick pixel cell so preview fits roughly in the editor pane."""
    return max(2, min(22, 360 // max(w, 1), 220 // max(h, 1)))


def parse_wh(text: str) -> tuple[int, int] | None:
    """Parse '22x26' / '22×26' / '22 26' → (w, h)."""
    s = (text or "").strip().lower().replace("×", "x").replace(",", "x").replace(" ", "x")
    parts = [p for p in s.split("x") if p]
    if len(parts) != 2:
        return None
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if w < 1 or h < 1 or w > CUSTOM_MAX or h > CUSTOM_MAX:
        return None
    return w, h


# Known icon canvas sizes → (edit_target, size_mode)
ICON_CANVAS_PRESETS: dict[tuple[int, int], tuple[str, str]] = {
    (16, 16): ("16", "16"),
    (11, 7): ("11", "11"),
    (6, 7): ("6", "6"),
    (22, 26): ("22", "22"),
    (84, 48): ("84", "84"),
    (72, 28): ("72", "72"),
}

IMAGE_IMPORT_EXTS = {".png", ".bmp", ".gif", ".jpg", ".jpeg", ".webp"}


def detect_wh_from_filename(name: str) -> tuple[int, int] | None:
    """Parse icon_22x26.png / book_72x28 / 16x16 → (w, h)."""
    s = Path(name).stem.lower().replace("×", "x")
    for pat in (
        r"(\d+)x(\d+)\s*$",
        r"[_-](\d+)x(\d+)(?:[_-]|$)",
        r"(\d+)x(\d+)",
    ):
        m = re.search(pat, s)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if 1 <= w <= CUSTOM_MAX and 1 <= h <= CUSTOM_MAX:
                return w, h
    return None


def resolve_icon_wh(path: Path, im: Image.Image) -> tuple[int, int, bool, str]:
    """Pick target WxH and whether to NEAREST-resize. Returns (w, h, resize, source)."""
    iw, ih = im.size
    if (iw, ih) in ICON_CANVAS_PRESETS:
        return iw, ih, False, "file dimensions"
    fn = detect_wh_from_filename(path.name)
    if fn:
        if fn in ICON_CANVAS_PRESETS:
            return fn[0], fn[1], fn != (iw, ih), "filename"
        return fn[0], fn[1], fn != (iw, ih), "filename"
    return iw, ih, False, "file dimensions"


def iter_image_files(folder: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_IMPORT_EXTS:
            out.append(p)
    return out

THEME = {
    # Dark blue UI
    "bg": "#0b1220",
    "panel": "#121a2b",
    "fg": "#e6eefc",
    "muted": "#8fa3c1",
    "accent": "#2f6fed",
    "accent_hi": "#4b86ff",
    "entry_bg": "#182338",
    "list_bg": "#152033",
    "list_sel": "#1e4fbf",
    "preview_bg": "#080e18",
    "border": "#243552",
    "ink": "#1a1a1a",
    "paper": "#c8d0a8",  # LCD paper tone
    "grid": "#6a7350",
    "titlebar": 0x000000,  # COLORREF black (Windows caption)
}


@dataclass
class Layer:
    path: Path
    image: Image.Image
    name: str
    angle: float = 0.0
    contrast: float = 1.0
    ox: int = 0
    oy: int = 0
    visible: bool = True
    scale: float = 1.0


def sanitize(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    return name.strip("_").lower() or "icon"


def to_mono(img: Image.Image, threshold: int) -> Image.Image:
    g = img.convert("L")
    return g.point(lambda p: 0 if p < threshold else 255, mode="1")


def pack_bytes(bw: Image.Image) -> list[int]:
    w, h = bw.size
    px = bw.load()
    rb = (w + 7) // 8
    out: list[int] = []
    for y in range(h):
        for b in range(rb):
            byte = 0
            for bit in range(8):
                x = b * 8 + bit
                if x < w and px[x, y] == 0:
                    byte |= 1 << (7 - bit)
            out.append(byte)
    return out


def ascii_rows(bw: Image.Image) -> list[str]:
    w, h = bw.size
    px = bw.load()
    return ["".join("#" if px[x, y] == 0 else "." for x in range(w)) for y in range(h)]


def c_array(name: str, w: int, h: int, data: list[int]) -> str:
    hexes = ", ".join(f"0x{b:02X}" for b in data)
    return (
        f"/* {name} {w}x{h} — MSB-left row-major */\n"
        f"static const uint8_t icon_{name}_{w}x{h}_bits[] PROGMEM = {{ {hexes} }};\n"
        f"/* icon_table entry: {{ {w}, {h}, icon_{name}_{w}x{h}_bits }} */\n"
    )


def c_anim(name: str, w: int, h: int, frames: list[list[int]], frame_ms: int) -> str:
    """Packed animation: all frames concatenated + icon_anim_t for firmware."""
    bpf = ((w + 7) // 8) * h
    sym = f"anim_{name}_{w}x{h}"
    lines = [
        f"/* Animation {name} {w}x{h}, {len(frames)} frames, {frame_ms} ms",
        f" * Firmware:",
        f" *   #include \"icons.h\"",
        f" *   #include \"{name}_{w}x{h}_anim.h\"   // or declare: extern const icon_anim_t {sym};",
        f" *   // compile/link this .c with the phone firmware",
        f" *   icon_anim_draw(fb, x, y, &{sym}, now_ms, 0);",
        f" */",
        f"#include \"icons.h\"",
        "",
        f"static const uint8_t {sym}_data[] PROGMEM = {{",
    ]
    for fi, data in enumerate(frames):
        assert len(data) == bpf, (len(data), bpf)
        hx = ", ".join(f"0x{b:02X}" for b in data)
        lines.append(f"  /* frame {fi} */ {hx},")
    lines.append("};")
    lines.append("")
    lines.append(
        f"const icon_anim_t {sym} = {{\n"
        f"  {w}, {h}, {len(frames)}, {int(frame_ms)}, {bpf},\n"
        f"  {sym}_data\n"
        f"}};\n"
    )
    return "\n".join(lines) + "\n"


def _safe_paste(base: Image.Image, im: Image.Image, x: int, y: int) -> None:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    bw, bh = base.size
    iw, ih = im.size
    dx0, dy0 = max(0, x), max(0, y)
    dx1, dy1 = min(bw, x + iw), min(bh, y + ih)
    if dx0 >= dx1 or dy0 >= dy1:
        return
    crop = im.crop((dx0 - x, dy0 - y, dx0 - x + dx1 - dx0, dy0 - y + dy1 - dy0))
    base.alpha_composite(crop, (dx0, dy0))


# ---- prompt generator (kept) -------------------------------------------------
# ---- pixel bitmap + editor ---------------------------------------------------

class PixelBitmap:
    """True = black ink on LCD."""

    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        self.data = [[False] * w for _ in range(h)]
        self._undo: list[list[list[bool]]] = []
        self._redo: list[list[list[bool]]] = []

    def clear(self, ink: bool = False) -> None:
        self.data = [[ink] * self.w for _ in range(self.h)]

    def resize(self, w: int, h: int, keep: bool = True) -> None:
        """Change canvas size; optionally keep top-left overlap."""
        w = max(1, int(w))
        h = max(1, int(h))
        old = self.copy_data() if keep else None
        ow, oh = self.w, self.h
        self.w, self.h = w, h
        self.data = [[False] * w for _ in range(h)]
        if old:
            for y in range(min(oh, h)):
                for x in range(min(ow, w)):
                    self.data[y][x] = old[y][x]
        self._undo.clear()
        self._redo.clear()

    def copy_data(self) -> list[list[bool]]:
        return copy.deepcopy(self.data)

    def push_undo(self) -> None:
        self._undo.append(self.copy_data())
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.copy_data())
        self.data = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.copy_data())
        self.data = self._redo.pop()
        return True

    def get(self, x: int, y: int) -> bool:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.data[y][x]
        return False

    def set(self, x: int, y: int, ink: bool) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.data[y][x] = ink

    def stamp(self, x: int, y: int, ink: bool, brush: int) -> None:
        r = max(0, brush - 1)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r + r:
                    self.set(x + dx, y + dy, ink)

    def from_pil(self, mono: Image.Image) -> None:
        im = mono.convert("1")
        if im.size != (self.w, self.h):
            im = im.resize((self.w, self.h), Image.Resampling.NEAREST)
        px = im.load()
        for y in range(self.h):
            for x in range(self.w):
                self.data[y][x] = px[x, y] == 0

    def to_pil(self) -> Image.Image:
        im = Image.new("1", (self.w, self.h), 1)
        px = im.load()
        for y in range(self.h):
            for x in range(self.w):
                if self.data[y][x]:
                    px[x, y] = 0
        return im

    def invert(self) -> None:
        for y in range(self.h):
            for x in range(self.w):
                self.data[y][x] = not self.data[y][x]

    def flip_h(self) -> None:
        for y in range(self.h):
            self.data[y].reverse()

    def flip_v(self) -> None:
        self.data.reverse()

    def rotate_cw(self) -> None:
        # only square-ish; for non-square keep bounding and crop/pad center
        old = self.copy_data()
        nw, nh = self.h, self.w
        nd = [[False] * nw for _ in range(nh)]
        for y in range(self.h):
            for x in range(self.w):
                nd[x][self.h - 1 - y] = old[y][x]
        # fit back into same w×h
        self.clear(False)
        ox = (self.w - nw) // 2
        oy = (self.h - nh) // 2
        for y in range(nh):
            for x in range(nw):
                self.set(x + ox, y + oy, nd[y][x])

    def shift(self, dx: int, dy: int) -> None:
        old = self.copy_data()
        self.clear(False)
        for y in range(self.h):
            for x in range(self.w):
                if old[y][x]:
                    self.set(x + dx, y + dy, True)

    def dilate(self) -> None:
        old = self.copy_data()
        for y in range(self.h):
            for x in range(self.w):
                if any(
                    old[yy][xx]
                    for yy in range(max(0, y - 1), min(self.h, y + 2))
                    for xx in range(max(0, x - 1), min(self.w, x + 2))
                ):
                    self.data[y][x] = True

    def erode(self) -> None:
        old = self.copy_data()
        for y in range(self.h):
            for x in range(self.w):
                self.data[y][x] = all(
                    old[yy][xx]
                    for yy in range(max(0, y - 1), min(self.h, y + 2))
                    for xx in range(max(0, x - 1), min(self.w, x + 2))
                    if abs(yy - y) + abs(xx - x) <= 1
                ) and old[y][x]

    def keep_rect(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Clear pixels outside inclusive rectangle (crop-in-place)."""
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        for y in range(self.h):
            for x in range(self.w):
                if not (xa <= x <= xb and ya <= y <= yb):
                    self.data[y][x] = False

    def content_bbox(self) -> tuple[int, int, int, int] | None:
        xs, ys = [], []
        for y in range(self.h):
            for x in range(self.w):
                if self.data[y][x]:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def flood(self, x: int, y: int, ink: bool) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        target = self.data[y][x]
        if target == ink:
            return
        stack = [(x, y)]
        while stack:
            cx, cy = stack.pop()
            if not (0 <= cx < self.w and 0 <= cy < self.h):
                continue
            if self.data[cy][cx] != target:
                continue
            self.data[cy][cx] = ink
            stack.extend([(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)])

    def line(self, x0: int, y0: int, x1: int, y1: int, ink: bool, brush: int) -> None:
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        while True:
            self.stamp(x0, y0, ink, brush)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def rect(self, x0: int, y0: int, x1: int, y1: int, ink: bool, fill: bool, brush: int) -> None:
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        if fill:
            for y in range(ya, yb + 1):
                for x in range(xa, xb + 1):
                    self.stamp(x, y, ink, brush)
        else:
            for x in range(xa, xb + 1):
                self.stamp(x, ya, ink, brush)
                self.stamp(x, yb, ink, brush)
            for y in range(ya, yb + 1):
                self.stamp(xa, y, ink, brush)
                self.stamp(xb, y, ink, brush)

    def ellipse(self, x0: int, y0: int, x1: int, y1: int, ink: bool, fill: bool, brush: int) -> None:
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        if xb == xa or yb == ya:
            self.line(xa, ya, xb, yb, ink, brush)
            return
        cx, cy = (xa + xb) / 2.0, (ya + yb) / 2.0
        rx, ry = max(0.5, (xb - xa) / 2.0), max(0.5, (yb - ya) / 2.0)
        for y in range(ya, yb + 1):
            for x in range(xa, xb + 1):
                nx, ny = (x - cx) / rx, (y - cy) / ry
                v = nx * nx + ny * ny
                if fill:
                    if v <= 1.05:
                        self.stamp(x, y, ink, brush)
                else:
                    if 0.75 <= v <= 1.15:
                        self.stamp(x, y, ink, brush)


TOOLS = [
    ("move", "Pan (move)"),
    ("pencil", "Pencil"),
    ("eraser", "Eraser"),
    ("toggle", "Toggle pixel"),
    ("fill", "Fill"),
    ("line", "Line"),
    ("rect", "Rect outline"),
    ("rectfill", "Rect fill"),
    ("ellipse", "Ellipse outline"),
    ("ellipsefill", "Ellipse fill"),
    ("crop", "Crop (marquee)"),
]


class PixelCanvas(tk.Canvas):
    def __init__(self, master, bmp: PixelBitmap, cell: int, tool_var: tk.StringVar,
                 brush_var: tk.IntVar, ink_var: tk.BooleanVar, on_change, **kw):
        self.bmp = bmp
        self.cell = cell
        self.tool_var = tool_var
        self.brush_var = brush_var
        self.ink_var = ink_var
        self.on_change = on_change
        w = bmp.w * cell + 1
        h = bmp.h * cell + 1
        super().__init__(master, width=w, height=h, bg=THEME["preview_bg"],
                         highlightthickness=1, highlightbackground=THEME["border"], **kw)
        self._drag = False
        self._x0 = self._y0 = 0
        self._last_x = self._last_y = 0
        self._crop_box: tuple[int, int, int, int] | None = None
        self._preview = None
        self._photo: ImageTk.PhotoImage | None = None
        self.bind("<ButtonPress-1>", self._down)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._up)
        self.bind("<ButtonPress-3>", self._right)
        self.redraw()

    def reconfigure(self, cell: int | None = None) -> None:
        """Refresh scrollregion / widget size after bitmap resize."""
        if cell is not None:
            self.cell = cell
        cw = min(420, self.bmp.w * self.cell + 1)
        ch = min(280, self.bmp.h * self.cell + 1)
        self.configure(
            scrollregion=(0, 0, self.bmp.w * self.cell + 1, self.bmp.h * self.cell + 1),
            width=cw,
            height=ch,
        )
        self.redraw()

    def _xy(self, e) -> tuple[int, int]:
        return int(self.canvasx(e.x)) // self.cell, int(self.canvasy(e.y)) // self.cell

    def redraw(self, overlay: PixelBitmap | None = None) -> None:
        self.delete("all")
        src = overlay.data if overlay else self.bmp.data
        if self.bmp.w * self.bmp.h > 300:
            im = Image.new("RGB", (self.bmp.w, self.bmp.h), (200, 208, 168))
            px = im.load()
            ink = (26, 26, 26)
            for y in range(self.bmp.h):
                for x in range(self.bmp.w):
                    if src[y][x]:
                        px[x, y] = ink
            big = im.resize(
                (self.bmp.w * self.cell, self.bmp.h * self.cell),
                Image.Resampling.NEAREST,
            )
            self._photo = ImageTk.PhotoImage(big)
            self.create_image(0, 0, anchor=tk.NW, image=self._photo)
        else:
            for y in range(self.bmp.h):
                for x in range(self.bmp.w):
                    x0, y0 = x * self.cell, y * self.cell
                    fill = THEME["ink"] if src[y][x] else THEME["paper"]
                    self.create_rectangle(
                        x0, y0, x0 + self.cell, y0 + self.cell,
                        fill=fill, outline=THEME["grid"], width=1,
                    )
        if self._crop_box:
            xa, ya, xb, yb = self._crop_box
            self.create_rectangle(
                xa * self.cell, ya * self.cell,
                (xb + 1) * self.cell, (yb + 1) * self.cell,
                outline="#ff7043", width=2,
            )

    def _down(self, e) -> None:
        x, y = self._xy(e)
        tool = self.tool_var.get()
        brush = int(self.brush_var.get())
        ink = bool(self.ink_var.get())
        self._x0, self._y0 = x, y
        self._last_x, self._last_y = x, y
        self._drag = True
        self._crop_box = None
        if tool == "move":
            self.bmp.push_undo()
            self.configure(cursor="fleur")
            return
        if tool == "crop":
            self.configure(cursor="crosshair")
            self._crop_box = (x, y, x, y)
            self.redraw()
            return
        self.configure(cursor="")
        if tool in ("pencil", "eraser", "toggle", "fill"):
            self.bmp.push_undo()
            if tool == "pencil":
                self.bmp.stamp(x, y, True, brush)
            elif tool == "eraser":
                self.bmp.stamp(x, y, False, brush)
            elif tool == "toggle":
                self.bmp.set(x, y, not self.bmp.get(x, y))
            elif tool == "fill":
                self.bmp.flood(x, y, ink)
            self.redraw()
            self.on_change()

    def _move(self, e) -> None:
        if not self._drag:
            return
        x, y = self._xy(e)
        tool = self.tool_var.get()
        brush = int(self.brush_var.get())
        if tool == "move":
            dx, dy = x - self._last_x, y - self._last_y
            if dx or dy:
                self.bmp.shift(dx, dy)
                self._last_x, self._last_y = x, y
                self.redraw()
                self.on_change()
            return
        if tool == "pencil":
            self.bmp.stamp(x, y, True, brush)
            self.redraw()
            self.on_change()
        elif tool == "eraser":
            self.bmp.stamp(x, y, False, brush)
            self.redraw()
            self.on_change()
        elif tool == "crop":
            xa, xb = sorted((self._x0, x))
            ya, yb = sorted((self._y0, y))
            self._crop_box = (xa, ya, xb, yb)
            tmp = PixelBitmap(self.bmp.w, self.bmp.h)
            tmp.data = self.bmp.copy_data()
            tmp.keep_rect(xa, ya, xb, yb)
            self.redraw(tmp)
        elif tool in ("line", "rect", "rectfill", "ellipse", "ellipsefill"):
            tmp = PixelBitmap(self.bmp.w, self.bmp.h)
            tmp.data = self.bmp.copy_data()
            ink = bool(self.ink_var.get())
            if tool == "line":
                tmp.line(self._x0, self._y0, x, y, ink, brush)
            elif tool == "rect":
                tmp.rect(self._x0, self._y0, x, y, ink, False, brush)
            elif tool == "rectfill":
                tmp.rect(self._x0, self._y0, x, y, ink, True, brush)
            elif tool == "ellipse":
                tmp.ellipse(self._x0, self._y0, x, y, ink, False, brush)
            else:
                tmp.ellipse(self._x0, self._y0, x, y, ink, True, brush)
            self.redraw(tmp)

    def _up(self, e) -> None:
        if not self._drag:
            return
        self._drag = False
        x, y = self._xy(e)
        tool = self.tool_var.get()
        brush = int(self.brush_var.get())
        ink = bool(self.ink_var.get())
        if tool == "move":
            self.configure(cursor="")
            self.redraw()
            return
        if tool == "crop":
            self.configure(cursor="")
            self.bmp.push_undo()
            self.bmp.keep_rect(self._x0, self._y0, x, y)
            self._crop_box = None
            self.redraw()
            self.on_change()
            return
        if tool in ("line", "rect", "rectfill", "ellipse", "ellipsefill"):
            self.bmp.push_undo()
            if tool == "line":
                self.bmp.line(self._x0, self._y0, x, y, ink, brush)
            elif tool == "rect":
                self.bmp.rect(self._x0, self._y0, x, y, ink, False, brush)
            elif tool == "rectfill":
                self.bmp.rect(self._x0, self._y0, x, y, ink, True, brush)
            elif tool == "ellipse":
                self.bmp.ellipse(self._x0, self._y0, x, y, ink, False, brush)
            else:
                self.bmp.ellipse(self._x0, self._y0, x, y, ink, True, brush)
            self.redraw()
            self.on_change()

    def _right(self, e) -> None:
        x, y = self._xy(e)
        self.ink_var.set(self.bmp.get(x, y))


class IconStudio(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Icon Studio")
        self._shell_chrome_applied = False
        self.withdraw()
        apply_window_icon(self, self)
        self.geometry("1280x820")
        self.minsize(1100, 700)

        self.layers: list[Layer] = []
        self.sel = tk.IntVar(value=-1)
        self.threshold = tk.IntVar(value=128)
        self.invert = tk.BooleanVar(value=False)
        self.out_name = tk.StringVar(value="my_icon")
        self.size_mode = tk.StringVar(value="both")
        self.status = tk.StringVar(value="Import / generate → edit pixels → save")
        self.prompt = tk.StringVar(value="")
        self.gen_count = tk.IntVar(value=1)
        self.auto_from_layers = tk.BooleanVar(value=True)
        self._gen_serial = 0
        self.tool = tk.StringVar(value="move")
        self.brush = tk.IntVar(value=1)
        self.draw_ink = tk.BooleanVar(value=True)
        self.edit_target = tk.StringVar(value="16")  # which transforms apply to
        self._loading_params = False
        self._ui_ready = False
        self._photo_comp: ImageTk.PhotoImage | None = None

        self.bmp16 = PixelBitmap(16, 16)
        self.bmp11 = PixelBitmap(11, 7)
        self.bmp6 = PixelBitmap(6, 7)
        self.bmp22 = PixelBitmap(22, 26)
        self.bmp_custom = PixelBitmap(22, 26)  # editable WxH via UI
        self.bmp84 = PixelBitmap(84, 48)  # full 84x48 LCD canvas
        self.bmp72 = PixelBitmap(72, 28)
        self.custom_w = tk.StringVar(value="22")
        self.custom_h = tk.StringVar(value="26")
        self.anim_custom = tk.StringVar(value="22x26")
        self.anim_frames: list[PixelBitmap] = []
        self.anim_size = tk.StringVar(value="22x26")
        self.anim_delay = tk.IntVar(value=200)
        self.anim_playing = False
        self._anim_job = None
        self._anim_idx = 0
        self._photo_anim = None

        self._apply_dark_theme()
        self._build_menu()
        self._build_ui()
        self._ui_ready = True
        self._bind_keys()
        self._refresh_all()
        self.bind("<Map>", lambda _e: self._apply_shell_appearance())
        for ms in (1, 50, 150, 400, 900):
            self.after(ms, self._apply_shell_appearance)
        self.after(20, self._show_main_window)

    def _show_main_window(self) -> None:
        self._apply_shell_appearance()
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.deiconify()
        self.lift()
        self._apply_shell_appearance()

    def _hwnd(self) -> int:
        """Resolve Win32 HWND for the Tk toplevel."""
        import ctypes

        self.update_idletasks()
        user32 = ctypes.windll.user32
        GA_ROOT = 2
        wid = int(self.winfo_id())
        root = int(user32.GetAncestor(wid, GA_ROOT))
        if root:
            return root
        hwnd = wid
        for _ in range(16):
            parent = int(user32.GetParent(hwnd))
            if not parent:
                break
            hwnd = parent
        return hwnd

    def _apply_windows_chrome(self) -> None:
        """Force black title bar on Win10/11 (DWM)."""
        import ctypes

        hwnd = self._hwnd()
        if not hwnd:
            return
        dwm = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        for attr, val in ((20, 1), (19, 1)):
            enabled = ctypes.c_int(val)
            dwm.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )

        black = ctypes.c_uint(0x00000000)
        white = ctypes.c_uint(0x00FFFFFF)
        for attr, color in ((35, black), (34, black), (36, white)):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(color), 4)

        SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0004, 0x0020
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )

    def _apply_shell_appearance(self) -> None:
        """Dark caption + logo icon (repeat until window is ready)."""
        try:
            self._apply_windows_chrome()
            apply_window_icon(self, self)
            self._shell_chrome_applied = True
        except Exception:
            pass

    def _apply_dark_theme(self) -> None:
        self.configure(bg=THEME["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=THEME["panel"], foreground=THEME["fg"],
                        fieldbackground=THEME["entry_bg"])
        style.configure("TFrame", background=THEME["panel"])
        style.configure("TLabel", background=THEME["panel"], foreground=THEME["fg"])
        style.configure("Hint.TLabel", background=THEME["bg"], foreground=THEME["muted"])
        style.configure("Muted.TLabel", background=THEME["panel"], foreground=THEME["muted"])
        style.configure("TButton", background=THEME["border"], foreground=THEME["fg"], padding=3)
        style.map("TButton",
                  background=[("active", THEME["accent"]), ("pressed", THEME["list_sel"])],
                  foreground=[("active", "#ffffff")])
        style.configure("Accent.TButton", background=THEME["accent"], foreground="#ffffff")
        style.map("Accent.TButton",
                  background=[("active", THEME["accent_hi"]), ("pressed", THEME["list_sel"])])
        style.configure("Tool.TRadiobutton", background=THEME["panel"], foreground=THEME["fg"])
        style.map("Tool.TRadiobutton",
                  background=[("active", THEME["entry_bg"])],
                  foreground=[("selected", THEME["accent_hi"])])
        style.configure("TCheckbutton", background=THEME["panel"], foreground=THEME["fg"])
        style.configure("TRadiobutton", background=THEME["panel"], foreground=THEME["fg"])
        style.map("TRadiobutton",
                  background=[("active", THEME["entry_bg"])],
                  foreground=[("selected", THEME["accent_hi"])])
        style.configure("TEntry", fieldbackground=THEME["entry_bg"], foreground=THEME["fg"],
                        insertcolor=THEME["fg"], bordercolor=THEME["border"],
                        lightcolor=THEME["border"], darkcolor=THEME["border"])
        style.configure("TSpinbox", fieldbackground=THEME["entry_bg"], foreground=THEME["fg"],
                        insertcolor=THEME["fg"], arrowsize=12)
        style.configure("Horizontal.TScale", background=THEME["panel"], troughcolor=THEME["entry_bg"])
        style.configure("TSeparator", background=THEME["border"])
        style.configure("TLabelframe", background=THEME["panel"], foreground=THEME["fg"])
        style.configure("TLabelframe.Label", background=THEME["panel"], foreground=THEME["fg"])
        style.configure("Status.TLabel", background=THEME["bg"], foreground=THEME["muted"])
        style.configure("TNotebook", background=THEME["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=THEME["entry_bg"], foreground=THEME["muted"],
                        padding=[12, 5], borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", THEME["accent"]), ("active", THEME["border"])],
                  foreground=[("selected", "#ffffff"), ("active", THEME["fg"])])
        style.configure("Vertical.TScrollbar", background=THEME["border"], troughcolor=THEME["bg"],
                        arrowcolor=THEME["fg"])
        style.configure("Horizontal.TScrollbar", background=THEME["border"], troughcolor=THEME["bg"],
                        arrowcolor=THEME["fg"])

    def _build_menu(self) -> None:
        # Native Win32 menubar stays white on Windows — use black custom bar instead
        self.config(menu="")
        bar_bg = "#000000"
        bar = tk.Frame(self, bg=bar_bg, highlightthickness=0)
        bar.pack(side=tk.TOP, fill=tk.X)
        self._menubar_frame = bar

        def _mb(text: str) -> tk.Menubutton:
            mb = tk.Menubutton(
                bar, text=text, bg=bar_bg, fg=THEME["fg"],
                activebackground=THEME["accent"], activeforeground="#ffffff",
                relief=tk.FLAT, bd=0, padx=12, pady=4,
                font=("", 9),
            )
            menu = tk.Menu(
                mb, tearoff=0, bg=bar_bg, fg=THEME["fg"],
                activebackground=THEME["accent"], activeforeground="#ffffff",
                bd=0, relief=tk.FLAT,
            )
            mb.configure(menu=menu)
            mb.pack(side=tk.LEFT)
            return mb, menu

        _file_mb, file_m = _mb("File")
        file_m.add_command(label="Import…\tCtrl+O", command=self.import_images)
        file_m.add_command(label="Icon to tab…", command=self.import_icon_file)
        file_m.add_command(label="Icons from folder…", command=self.import_icon_directory)
        file_m.add_command(label="Paste screenshot…\tCtrl+V", command=self.paste_clipboard)
        file_m.add_command(label="Generate…\tCtrl+G", command=self.generate_from_prompt)
        file_m.add_command(label="Save\tCtrl+S", command=self.save_icons)
        file_m.add_separator()
        file_m.add_command(label="Open ready_icons", command=self.open_ready)
        file_m.add_command(label="Exit", command=self.destroy)

        _edit_mb, edit_m = _mb("Edit")
        edit_m.add_command(label="Undo\tCtrl+Z", command=self.edit_undo)
        edit_m.add_command(label="Redo\tCtrl+Y", command=self.edit_redo)
        edit_m.add_separator()
        edit_m.add_command(label="Refresh pixels from layers", command=self.pull_from_layers)

        _help_mb, help_m = _mb("Help")
        help_m.add_command(label="Help", command=self.show_help)

    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda e: self.import_images())
        self.bind("<Control-s>", lambda e: self.save_icons())
        self.bind("<Control-g>", lambda e: self.generate_from_prompt())
        self.bind("<Control-v>", lambda e: self.paste_clipboard())
        self.bind("<Control-V>", lambda e: self.paste_clipboard())

        def _undo(_e=None):
            self.edit_undo()
            return "break"

        def _redo(_e=None):
            self.edit_redo()
            return "break"

        # Latin keysyms only — Tk rejects non-Latin keysyms
        for seq in ("<Control-z>", "<Control-Z>"):
            self.bind_all(seq, _undo)
        for seq in ("<Control-y>", "<Control-Y>"):
            self.bind_all(seq, _redo)

        # RU layout: physical Z is keycode 90; catch Ctrl+that without bad keysyms
        def _ctrl_letter(e):
            if not (e.state & 0x4):
                return None
            kc = getattr(e, "keycode", 0)
            if kc == 90:  # Z
                self.edit_undo()
                return "break"
            if kc == 89:  # Y
                self.edit_redo()
                return "break"
            return None

        self.bind_all("<Control-KeyPress>", _ctrl_letter)


    def _scrollable_frame(self, parent, width: int = 220) -> ttk.Frame:
        """Vertical-scrollable column; returns the inner content frame."""
        outer = ttk.Frame(parent)
        canvas = tk.Canvas(
            outer, borderwidth=0, highlightthickness=0,
            bg=THEME["panel"], width=width,
        )
        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas, padding=4)
        win = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _sync_scroll(_e=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_width(e):
            canvas.itemconfigure(win, width=max(width - 18, e.width))

        inner.bind("<Configure>", _sync_scroll)
        canvas.bind("<Configure>", _sync_width)
        canvas.configure(yscrollcommand=vsb.set)

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind_wheel(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _unbind_wheel(_e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        return inner

    def _build_ui(self) -> None:
        ttk.Label(
            self,
            text="1) Import / generate  →  2) Layers / threshold  →  3) Pixel edit  →  4) Save",
            style="Hint.TLabel", padding=(10, 4),
        ).pack(fill=tk.X)

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)

        # LEFT: layers (vertical scroll)
        left_wrap = ttk.Frame(body)
        left_wrap.pack(side=tk.LEFT, fill=tk.Y)
        left = self._scrollable_frame(left_wrap, width=230)
        ttk.Label(left, text="Layers", font=("", 10, "bold")).pack(anchor=tk.W)
        self.listbox = tk.Listbox(
            left, height=8, exportselection=False, width=22,
            bg=THEME["list_bg"], fg=THEME["fg"], selectbackground=THEME["list_sel"],
            highlightthickness=0,
        )
        self.listbox.pack(fill=tk.X)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        ttk.Button(left, text="Import…", command=self.import_images).pack(fill=tk.X, pady=2)
        ttk.Button(left, text="Paste screenshot (Ctrl+V)", command=self.paste_clipboard).pack(fill=tk.X, pady=2)
        ttk.Button(left, text="Delete layer", command=self.remove_layer).pack(fill=tk.X)
        row = ttk.Frame(left)
        row.pack(fill=tk.X, pady=2)
        ttk.Button(row, text="↑", command=lambda: self.move_layer(-1), width=4).pack(side=tk.LEFT, expand=True)
        ttk.Button(row, text="↓", command=lambda: self.move_layer(1), width=4).pack(side=tk.LEFT, expand=True)

        self.ang = tk.DoubleVar(value=0)
        self.con = tk.DoubleVar(value=1.0)
        self.ox = tk.DoubleVar(value=0)
        self.oy = tk.DoubleVar(value=0)
        self.sc = tk.DoubleVar(value=1.0)
        self.layer_visible = tk.BooleanVar(value=True)
        self.crop_x = tk.IntVar(value=0)
        self.crop_y = tk.IntVar(value=0)
        self.crop_w = tk.IntVar(value=16)
        self.crop_h = tk.IntVar(value=16)
        self.comp_mode = tk.StringVar(value="move")  # move | crop
        ttk.Label(left, text="Layer (type numbers)", font=("", 9, "bold")).pack(
            anchor=tk.W, pady=(6, 0)
        )
        self._num_param(left, "Rotation", self.ang, -180, 180, self._on_layer_param, "{:.1f}")
        self._num_param(left, "Contrast", self.con, 0.1, 4.0, self._on_layer_param, "{:.2f}")
        self._num_param(left, "Scale", self.sc, 0.05, 8.0, self._on_layer_param, "{:.3f}")
        self._num_param(left, "Offset X", self.ox, -256, 256, self._on_layer_param, "{:.0f}")
        self._num_param(left, "Offset Y", self.oy, -256, 256, self._on_layer_param, "{:.0f}")
        ttk.Checkbutton(left, text="Visible", variable=self.layer_visible,
                        command=self._on_visibility).pack(anchor=tk.W)
        self._num_param(left, "Threshold", self.threshold, 1, 254, lambda *_: self._on_threshold(), "{:.0f}")
        ttk.Checkbutton(left, text="Invert layers", variable=self.invert,
                        command=self._on_threshold).pack(anchor=tk.W)

        cropf = ttk.LabelFrame(left, text="Layer crop", padding=4)
        cropf.pack(fill=tk.X, pady=4)
        for lab, var in (
            ("X", self.crop_x),
            ("Y", self.crop_y),
            ("W", self.crop_w),
            ("H", self.crop_h),
        ):
            rowc = ttk.Frame(cropf)
            rowc.pack(fill=tk.X, pady=1)
            ttk.Label(rowc, text=lab, width=3).pack(side=tk.LEFT)
            ttk.Entry(rowc, textvariable=var, width=7).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(cropf, text="Crop by X/Y/W/H", command=self.crop_layer_box).pack(fill=tk.X, pady=2)
        ttk.Button(cropf, text="Crop to content", command=self.crop_layer_content).pack(fill=tk.X)
        ttk.Label(
            cropf,
            text="Or use Crop mode on\ncomposition: drag a marquee.",
            style="Muted.TLabel", wraplength=180,
        ).pack(anchor=tk.W, pady=2)

        gen = ttk.LabelFrame(left, text="Generate", padding=4)
        gen.pack(fill=tk.X, pady=6)
        ttk.Entry(gen, textvariable=self.prompt).pack(fill=tk.X)
        crow = ttk.Frame(gen)
        crow.pack(fill=tk.X, pady=2)
        ttk.Label(crow, text="Variants:").pack(side=tk.LEFT)
        ttk.Spinbox(crow, from_=1, to=12, width=4, textvariable=self.gen_count).pack(side=tk.LEFT, padx=4)
        ttk.Button(gen, text="Generate (Ctrl+G)", style="Accent.TButton",
                   command=self.generate_from_prompt).pack(fill=tk.X, pady=2)
        ttk.Label(
            gen,
            text="Each run creates new variants.\nClick again for more.",
            style="Muted.TLabel",
            wraplength=180,
        ).pack(anchor=tk.W)

        # CENTER: top-level Icon editor | Animation
        mid = ttk.Frame(body, padding=6)
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.main_nb = ttk.Notebook(mid)
        self.main_nb.pack(fill=tk.BOTH, expand=True)

        editor_tab = ttk.Frame(self.main_nb)
        self.main_nb.add(editor_tab, text="Icon editor")
        anim_tab = ttk.Frame(self.main_nb)
        self.main_nb.add(anim_tab, text="Animation editor")
        self._build_anim_tab(anim_tab)

        top_mid = ttk.Frame(editor_tab)
        top_mid.pack(fill=tk.X)
        ttk.Label(top_mid, text="Composition", font=("", 10, "bold")).pack(anchor=tk.W)
        comp_col = ttk.Frame(top_mid)
        comp_col.pack(side=tk.LEFT, padx=(0, 12))
        self._comp_scale = COMP_VIEW / COMP_SIZE
        self.canvas_comp = tk.Canvas(
            comp_col, width=COMP_VIEW, height=COMP_VIEW,
            bg=THEME["preview_bg"], highlightthickness=1,
            highlightbackground=THEME["border"], cursor="fleur",
        )
        self.canvas_comp.pack()
        self.canvas_comp.bind("<ButtonPress-1>", self._comp_down)
        self.canvas_comp.bind("<B1-Motion>", self._comp_move)
        self.canvas_comp.bind("<ButtonRelease-1>", self._comp_up)
        modes = ttk.Frame(comp_col)
        modes.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(modes, text="Move", value="move", variable=self.comp_mode,
                        command=self._sync_comp_cursor).pack(side=tk.LEFT)
        ttk.Radiobutton(modes, text="Crop", value="crop", variable=self.comp_mode,
                        command=self._sync_comp_cursor).pack(side=tk.LEFT)
        ttk.Label(
            comp_col,
            text="LMB: move layer / crop marquee",
            style="Muted.TLabel",
        ).pack(anchor=tk.W)
        self._comp_drag = False
        self._comp_ox0 = 0
        self._comp_oy0 = 0
        self._comp_mx0 = 0
        self._comp_my0 = 0
        self._comp_crop_rect = None

        sync = ttk.Frame(top_mid)
        sync.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Checkbutton(
            sync, text="Auto-refresh pixels from layers",
            variable=self.auto_from_layers, command=self._on_auto_toggle,
        ).pack(anchor=tk.W)
        ttk.Button(sync, text="Pull from layers now", command=self.pull_from_layers).pack(fill=tk.X, pady=4)
        ttk.Label(
            sync,
            text="Uncheck to edit pixels manually\nwithout layer updates overwriting you.",
            style="Muted.TLabel", wraplength=220,
        ).pack(anchor=tk.W)

        pix = ttk.LabelFrame(editor_tab, text="Pixels (saved output)", padding=6)
        pix.pack(fill=tk.BOTH, expand=True, pady=6)

        nb = ttk.Notebook(pix)
        nb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.pix_nb = nb

        def _tab(title: str, bmp: PixelBitmap, cell: int) -> PixelCanvas:
            fr = ttk.Frame(nb, padding=4)
            nb.add(fr, text=title)
            wrap = ttk.Frame(fr)
            wrap.pack(fill=tk.BOTH, expand=True)
            cv = PixelCanvas(wrap, bmp, cell, self.tool, self.brush, self.draw_ink, self._on_pixels_edited)
            # Always allow vertical (+ horizontal) scroll for editors
            hsb = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=cv.xview)
            vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=cv.yview)
            cw = min(420, bmp.w * cell + 1)
            ch = min(280, bmp.h * cell + 1)
            cv.configure(
                xscrollcommand=hsb.set,
                yscrollcommand=vsb.set,
                scrollregion=(0, 0, bmp.w * cell + 1, bmp.h * cell + 1),
                width=cw,
                height=ch,
            )
            cv.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            wrap.rowconfigure(0, weight=1)
            wrap.columnconfigure(0, weight=1)
            return cv

        self.cv16 = _tab("16×16", self.bmp16, CELL16)
        self.cv11 = _tab("11×7", self.bmp11, CELL11)
        self.cv6 = _tab("6×7", self.bmp6, CELL6)
        self.cv22 = _tab("22×26", self.bmp22, CELL22)
        self.cv_custom = _tab(
            f"Custom {self.bmp_custom.w}×{self.bmp_custom.h}",
            self.bmp_custom,
            cell_for_size(self.bmp_custom.w, self.bmp_custom.h),
        )
        self.cv84 = _tab("84×48", self.bmp84, CELL84)
        self.cv72 = _tab("72×28", self.bmp72, CELL72)

        tools_wrap = ttk.Frame(pix)
        tools_wrap.pack(side=tk.LEFT, fill=tk.Y, padx=8)
        tools = self._scrollable_frame(tools_wrap, width=200)
        ttk.Label(tools, text="Tools", font=("", 10, "bold")).pack(anchor=tk.W)
        for tid, title in TOOLS:
            ttk.Radiobutton(tools, text=title, value=tid, variable=self.tool,
                            style="Tool.TRadiobutton").pack(anchor=tk.W)

        ttk.Separator(tools).pack(fill=tk.X, pady=4)
        ttk.Label(tools, text="Brush").pack(anchor=tk.W)
        br = ttk.Frame(tools)
        br.pack(anchor=tk.W)
        for n in (1, 2, 3, 4, 5):
            ttk.Radiobutton(br, text=str(n), value=n, variable=self.brush).pack(side=tk.LEFT)
        ttk.Checkbutton(tools, text="Draw with ink",
                        variable=self.draw_ink).pack(anchor=tk.W, pady=2)
        ttk.Label(tools, text="RMB = pick fill color", style="Muted.TLabel").pack(anchor=tk.W)

        ttk.Separator(tools).pack(fill=tk.X, pady=4)
        ttk.Label(tools, text="Load icon", font=("", 10, "bold")).pack(anchor=tk.W)
        ttk.Button(tools, text="From file…", command=self.import_icon_file).pack(fill=tk.X, pady=2)
        ttk.Button(tools, text="From folder…", command=self.import_icon_directory).pack(fill=tk.X, pady=2)
        ttk.Label(
            tools,
            text="W×H from image size\nor filename (icon_22x26.png).",
            style="Muted.TLabel", wraplength=180,
        ).pack(anchor=tk.W)

        ttk.Separator(tools).pack(fill=tk.X, pady=4)
        ttk.Label(tools, text="Custom canvas size", font=("", 10, "bold")).pack(anchor=tk.W)
        crow = ttk.Frame(tools)
        crow.pack(fill=tk.X, pady=2)
        ttk.Entry(crow, textvariable=self.custom_w, width=4).pack(side=tk.LEFT)
        ttk.Label(crow, text="×").pack(side=tk.LEFT, padx=2)
        ttk.Entry(crow, textvariable=self.custom_h, width=4).pack(side=tk.LEFT)
        ttk.Button(crow, text="OK", width=4, command=self.apply_custom_size).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            tools,
            text="Enter W×H (1–128) and OK.\nUses the Custom tab.",
            style="Muted.TLabel", wraplength=180,
        ).pack(anchor=tk.W)

        ttk.Separator(tools).pack(fill=tk.X, pady=4)
        ttk.Label(tools, text="Edit target", font=("", 10, "bold")).pack(anchor=tk.W)
        for val, lab in (
            ("16", "16×16"),
            ("11", "11×7"),
            ("6", "6×7"),
            ("22", "22×26"),
            ("custom", "Custom"),
            ("84", "84×48"),
            ("72", "72×28"),
            ("all", "All"),
        ):
            ttk.Radiobutton(tools, text=lab, value=val, variable=self.edit_target).pack(anchor=tk.W)

        actions = [
            ("Undo", self.edit_undo),
            ("Redo", self.edit_redo),
            ("Clear", self.edit_clear),
            ("Invert", self.edit_invert),
            ("Flip ↔", self.edit_flip_h),
            ("Flip ↕", self.edit_flip_v),
            ("Rotate 90°", self.edit_rot),
            ("Nudge ←", lambda: self.edit_shift(-1, 0)),
            ("Nudge →", lambda: self.edit_shift(1, 0)),
            ("Nudge ↑", lambda: self.edit_shift(0, -1)),
            ("Nudge ↓", lambda: self.edit_shift(0, 1)),
            ("Thicken", self.edit_dilate),
            ("Thin", self.edit_erode),
            ("Crop to content", self.edit_crop_content),
            ("16→11", self.edit_scale_16_to_11),
            ("11→16", self.edit_scale_11_to_16),
            ("→84×48", self.edit_scale_to_84),
            ("→72×28", self.edit_scale_to_72),
            ("84→72", self.edit_scale_84_to_72),
            ("72→84", self.edit_scale_72_to_84),
        ]
        af = ttk.Frame(tools)
        af.pack(fill=tk.X, pady=4)
        for i, (title, cmd) in enumerate(actions):
            ttk.Button(af, text=title, command=cmd).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=2, pady=1
            )
            af.columnconfigure(i % 2, weight=1)

        # RIGHT: save
        right = ttk.Frame(body, padding=6)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Label(right, text="Save", font=("", 10, "bold")).pack(anchor=tk.W)
        ttk.Entry(right, textvariable=self.out_name).pack(fill=tk.X, pady=4)
        ttk.Radiobutton(right, text="16×16 and 11×7", variable=self.size_mode, value="both").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="16×16 only", variable=self.size_mode, value="16").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="11×7 only", variable=self.size_mode, value="11").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="22×26", variable=self.size_mode, value="22").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="Custom size", variable=self.size_mode, value="custom").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="84×48 (full screen)", variable=self.size_mode, value="84").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="72×28", variable=self.size_mode, value="72").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="6×7", variable=self.size_mode, value="6").pack(anchor=tk.W)
        ttk.Radiobutton(right, text="All sizes", variable=self.size_mode, value="all").pack(anchor=tk.W)
        ttk.Button(right, text="Save (Ctrl+S)", style="Accent.TButton",
                   command=self.save_icons).pack(fill=tk.X, pady=8)
        ttk.Button(right, text="Open folder", command=self.open_ready).pack(fill=tk.X)
        ttk.Label(right, text=f"{READY}", style="Muted.TLabel", wraplength=180).pack(anchor=tk.W, pady=6)
        ttk.Label(
            right,
            text="Tabs: 16×16, 11×7, 6×7,\n22×26, Custom (manual WxH),\n"
                 "84×48, 72×28.\n\nPNG + TXT + C.",
            style="Muted.TLabel", wraplength=180,
        ).pack(anchor=tk.W)

        ttk.Label(self, textvariable=self.status, style="Status.TLabel",
                  anchor=tk.W, padding=(8, 4)).pack(side=tk.BOTTOM, fill=tk.X)



    def _build_anim_tab(self, host) -> None:
        """Full animation editor: glue 2+ icons → icon_anim_t C for firmware."""
        if isinstance(host, ttk.Notebook):
            fr = ttk.Frame(host, padding=8)
            host.add(fr, text="Animation editor")
        else:
            fr = host
            for c in list(fr.winfo_children()):
                c.destroy()

        ttk.Label(
            fr,
            text="Combine 2+ icons into an animation → save C (icon_anim_t) for icon_anim_draw() in firmware",
            font=("", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        top = ttk.Frame(fr)
        top.pack(fill=tk.X)

        # --- size + delay ---
        opts = ttk.LabelFrame(top, text="Parameters", padding=6)
        opts.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        ttk.Label(opts, text="Frame size:").pack(anchor=tk.W)
        for val, lab in (
            ("6x7", "6×7"),
            ("11x7", "11×7"),
            ("16x16", "16×16"),
            ("22x26", "22×26"),
            ("72x28", "72×28"),
            ("84x48", "84×48"),
        ):
            ttk.Radiobutton(opts, text=lab, value=val, variable=self.anim_size,
                            command=self._anim_on_size).pack(anchor=tk.W)
        ttk.Label(opts, text="Or custom (WxH):").pack(anchor=tk.W, pady=(6, 0))
        arow = ttk.Frame(opts)
        arow.pack(fill=tk.X, pady=2)
        ttk.Entry(arow, textvariable=self.anim_custom, width=8).pack(side=tk.LEFT)
        ttk.Button(arow, text="OK", width=4, command=self.apply_anim_custom_size).pack(side=tk.LEFT, padx=4)
        drow = ttk.Frame(opts)
        drow.pack(fill=tk.X, pady=6)
        ttk.Label(drow, text="ms/frame:").pack(side=tk.LEFT)
        ttk.Entry(drow, textvariable=self.anim_delay, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(opts, text="Name (same as icon):").pack(anchor=tk.W)
        ttk.Entry(opts, textvariable=self.out_name).pack(fill=tk.X)

        # --- add sources ---
        src = ttk.LabelFrame(top, text="Add frame", padding=6)
        src.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        ttk.Button(src, text="From canvas 6×7", command=lambda: self.anim_add_from_canvas("6")).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From canvas 11×7", command=lambda: self.anim_add_from_canvas("11")).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From canvas 16×16", command=lambda: self.anim_add_from_canvas("16")).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From canvas 22×26", command=lambda: self.anim_add_from_canvas("22")).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From custom canvas", command=lambda: self.anim_add_from_canvas("custom")).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From canvas 72×28", command=lambda: self.anim_add_from_canvas("72")).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From canvas 84×48", command=lambda: self.anim_add_from_canvas("84")).pack(fill=tk.X, pady=1)
        ttk.Separator(src).pack(fill=tk.X, pady=4)
        ttk.Button(src, text="PNG / multiple files…", command=self.anim_add_files).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="From ready_icons…", command=self.anim_add_ready).pack(fill=tk.X, pady=1)
        ttk.Button(src, text="Duplicate frame", command=self.anim_duplicate).pack(fill=tk.X, pady=1)

        # --- preview ---
        prev = ttk.LabelFrame(top, text="Preview", padding=6)
        prev.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.anim_canvas = tk.Canvas(
            prev, width=280, height=200, bg=THEME["preview_bg"],
            highlightthickness=1, highlightbackground=THEME["border"],
        )
        self.anim_canvas.pack()
        self.anim_status = tk.StringVar(value="Add at least 2 frames")
        ttk.Label(prev, textvariable=self.anim_status, style="Muted.TLabel").pack(anchor=tk.W, pady=4)
        brow = ttk.Frame(prev)
        brow.pack(fill=tk.X)
        ttk.Button(brow, text="▶ Play", command=self.anim_play_toggle).pack(side=tk.LEFT, padx=2)
        ttk.Button(brow, text="Clear all", command=self.anim_clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(brow, text="Save animation (C)", style="Accent.TButton",
                   command=self.anim_save).pack(side=tk.LEFT, padx=8)

        # --- frame list + filmstrip ---
        mid = ttk.Frame(fr)
        mid.pack(fill=tk.BOTH, expand=True, pady=8)

        list_fr = ttk.LabelFrame(mid, text="Frames (order = animation)", padding=4)
        list_fr.pack(side=tk.LEFT, fill=tk.Y)
        self.anim_list = tk.Listbox(
            list_fr, height=12, width=28, exportselection=False,
            bg=THEME["list_bg"], fg=THEME["fg"], selectbackground=THEME["list_sel"],
        )
        self.anim_list.pack(side=tk.LEFT, fill=tk.Y)
        sb = ttk.Scrollbar(list_fr, orient=tk.VERTICAL, command=self.anim_list.yview)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        self.anim_list.configure(yscrollcommand=sb.set)
        self.anim_list.bind("<<ListboxSelect>>", lambda _e: self._anim_preview_frame())
        ops = ttk.Frame(list_fr)
        ops.pack(fill=tk.X, pady=4)
        ttk.Button(ops, text="↑", width=4, command=lambda: self.anim_move(-1)).pack(side=tk.LEFT)
        ttk.Button(ops, text="↓", width=4, command=lambda: self.anim_move(1)).pack(side=tk.LEFT)
        ttk.Button(ops, text="Remove", command=self.anim_remove).pack(side=tk.LEFT, padx=4)

        strip_fr = ttk.LabelFrame(mid, text="Frame strip", padding=4)
        strip_fr.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.anim_strip = tk.Canvas(
            strip_fr, height=120, bg=THEME["preview_bg"],
            highlightthickness=1, highlightbackground=THEME["border"],
        )
        self.anim_strip.pack(fill=tk.BOTH, expand=True)
        self.anim_strip.bind("<Button-1>", self._anim_strip_click)
        self._photo_anim_strip = None

        ttk.Label(
            fr,
            text="After save: copy ready_icons/<name>_WxH_anim.c into your project and call "
                 "icon_anim_draw(fb, x, y, &anim_<name>_WxH, now_ms, 0);",
            style="Muted.TLabel", wraplength=720,
        ).pack(anchor=tk.W)

        self._anim_refresh_list()
        self._anim_preview_frame()

    def _anim_wh(self) -> tuple[int, int]:
        parsed = parse_wh(self.anim_size.get())
        if parsed:
            return parsed
        return 22, 26

    def _anim_source_bmp(self) -> PixelBitmap:
        w, h = self._anim_wh()
        mapping = {
            (16, 16): self.bmp16,
            (11, 7): self.bmp11,
            (6, 7): self.bmp6,
            (22, 26): self.bmp22,
            (72, 28): self.bmp72,
            (84, 48): self.bmp84,
        }
        if (w, h) in mapping:
            return mapping[(w, h)]
        if (self.bmp_custom.w, self.bmp_custom.h) == (w, h):
            return self.bmp_custom
        return self.bmp_custom

    def apply_anim_custom_size(self) -> None:
        parsed = parse_wh(self.anim_custom.get())
        if not parsed:
            messagebox.showerror(
                "Size",
                f"Enter W×H, e.g. 22x26 (1–{CUSTOM_MAX}).",
            )
            return
        w, h = parsed
        self.anim_size.set(f"{w}x{h}")
        self.anim_custom.set(f"{w}x{h}")
        self._anim_on_size()
        self._set_status(f"Animation: frame size {w}×{h}")

    def _anim_on_size(self) -> None:
        self._anim_refresh_list()
        self._anim_preview_frame()

    def _anim_make_frame_from_bmp(self, src: PixelBitmap) -> PixelBitmap:
        w, h = self._anim_wh()
        frame = PixelBitmap(w, h)
        if src.w == w and src.h == h:
            frame.data = src.copy_data()
        else:
            frame.from_pil(src.to_pil().resize((w, h), Image.Resampling.NEAREST))
        return frame

    def _anim_refresh_list(self) -> None:
        if not hasattr(self, "anim_list"):
            return
        self.anim_list.delete(0, tk.END)
        for i, fr in enumerate(self.anim_frames):
            self.anim_list.insert(tk.END, f"{i + 1}. frame {fr.w}×{fr.h}")
        n = len(self.anim_frames)
        if hasattr(self, "anim_status"):
            self.anim_status.set(f"Frames: {n}" + ("" if n >= 2 else " — need ≥2"))
        self._anim_redraw_strip()

    def _anim_redraw_strip(self) -> None:
        if not hasattr(self, "anim_strip"):
            return
        self.anim_strip.delete("all")
        if not self.anim_frames:
            self.anim_strip.create_text(20, 50, anchor=tk.W, text="Strip empty", fill=THEME["muted"])
            return
        pad, gap = 8, 6
        x = pad
        thumbs = []
        max_h = 100
        for i, fr in enumerate(self.anim_frames):
            cell = max(2, min(12, max_h // max(fr.h, 1)))
            im = Image.new("RGB", (fr.w, fr.h), (200, 208, 168))
            px = im.load()
            for yy in range(fr.h):
                for xx in range(fr.w):
                    if fr.data[yy][xx]:
                        px[xx, yy] = (26, 26, 26)
            big = im.resize((fr.w * cell, fr.h * cell), Image.Resampling.NEAREST)
            thumbs.append(ImageTk.PhotoImage(big))
            self.anim_strip.create_image(x, pad, anchor=tk.NW, image=thumbs[-1], tags=(f"f{i}",))
            self.anim_strip.create_text(x + big.width // 2, pad + big.height + 2,
                                       text=str(i + 1), fill=THEME["fg"], anchor=tk.N)
            x += big.width + gap
        self._photo_anim_strip = thumbs  # keep refs
        self.anim_strip.configure(scrollregion=(0, 0, x + pad, 120))

    def _anim_strip_click(self, e) -> None:
        items = self.anim_strip.find_overlapping(e.x, e.y, e.x, e.y)
        for it in items:
            for tag in self.anim_strip.gettags(it):
                if tag.startswith("f") and tag[1:].isdigit():
                    i = int(tag[1:])
                    self.anim_list.selection_clear(0, tk.END)
                    self.anim_list.selection_set(i)
                    self._anim_preview_frame(i)
                    return

    def anim_add_from_canvas(self, which: str) -> None:
        src = {
            "6": self.bmp6, "11": self.bmp11, "16": self.bmp16,
            "22": self.bmp22, "custom": self.bmp_custom,
            "72": self.bmp72, "84": self.bmp84,
        }.get(which, self.bmp6)
        self.anim_frames.append(self._anim_make_frame_from_bmp(src))
        self._anim_refresh_list()
        self.anim_list.selection_clear(0, tk.END)
        self.anim_list.selection_set(len(self.anim_frames) - 1)
        self._anim_preview_frame()
        self._set_status(f"Animation: +frame from {which}")

    def anim_add_current(self) -> None:
        self.anim_add_from_canvas({
            (6, 7): "6", (11, 7): "11", (16, 16): "16",
            (22, 26): "22", (72, 28): "72", (84, 48): "84",
            (self.bmp_custom.w, self.bmp_custom.h): "custom",
        }.get(self._anim_wh(), "custom"))

    def anim_add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Animation frames (multiple OK)",
            filetypes=[("Images", "*.png;*.bmp;*.gif;*.jpg;*.jpeg"), ("All", "*.*")],
        )
        if not paths:
            return
        w, h = self._anim_wh()
        added = 0
        for p in paths:
            try:
                im = Image.open(p).convert("RGBA")
            except Exception:
                continue
            mono = to_mono(im, int(self.threshold.get()))
            if mono.size != (w, h):
                mono = mono.resize((w, h), Image.Resampling.NEAREST)
            frame = PixelBitmap(w, h)
            frame.from_pil(mono)
            self.anim_frames.append(frame)
            added += 1
        self._anim_refresh_list()
        self._anim_preview_frame()
        self._set_status(f"Animation: +{added} from files")

    def anim_add_ready(self) -> None:
        READY.mkdir(parents=True, exist_ok=True)
        paths = filedialog.askopenfilenames(
            title="Pick icons from ready_icons",
            initialdir=str(READY),
            filetypes=[("PNG icons", "*.png"), ("All", "*.*")],
        )
        if not paths:
            return
        w, h = self._anim_wh()
        for p in paths:
            try:
                im = Image.open(p)
            except Exception:
                continue
            mono = to_mono(im.convert("RGBA"), int(self.threshold.get()))
            if mono.size != (w, h):
                mono = mono.resize((w, h), Image.Resampling.NEAREST)
            frame = PixelBitmap(w, h)
            frame.from_pil(mono)
            self.anim_frames.append(frame)
        self._anim_refresh_list()
        self._anim_preview_frame()

    def anim_duplicate(self) -> None:
        sel = self.anim_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        src = self.anim_frames[i]
        dup = PixelBitmap(src.w, src.h)
        dup.data = src.copy_data()
        self.anim_frames.insert(i + 1, dup)
        self._anim_refresh_list()
        self.anim_list.selection_set(i + 1)
        self._anim_preview_frame(i + 1)

    def anim_clear(self) -> None:
        self.anim_frames.clear()
        self.anim_playing = False
        self._anim_refresh_list()
        self._anim_preview_frame()

    def anim_remove(self) -> None:
        sel = self.anim_list.curselection()
        if not sel:
            return
        self.anim_frames.pop(int(sel[0]))
        self._anim_refresh_list()
        self._anim_preview_frame()

    def anim_move(self, delta: int) -> None:
        sel = self.anim_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        j = i + delta
        if j < 0 or j >= len(self.anim_frames):
            return
        self.anim_frames[i], self.anim_frames[j] = self.anim_frames[j], self.anim_frames[i]
        self._anim_refresh_list()
        self.anim_list.selection_set(j)
        self._anim_preview_frame(j)

    def _anim_preview_frame(self, idx: int | None = None) -> None:
        if not hasattr(self, "anim_canvas"):
            return
        self.anim_canvas.delete("all")
        if not self.anim_frames:
            self.anim_canvas.create_text(140, 100, text="no frames", fill=THEME["muted"])
            return
        if idx is None:
            sel = self.anim_list.curselection()
            idx = int(sel[0]) if sel else 0
        idx = max(0, min(idx, len(self.anim_frames) - 1))
        fr = self.anim_frames[idx]
        cell = max(4, min(24, 180 // max(fr.w, fr.h, 1)))
        im = Image.new("RGB", (fr.w, fr.h), (200, 208, 168))
        px = im.load()
        for y in range(fr.h):
            for x in range(fr.w):
                if fr.data[y][x]:
                    px[x, y] = (26, 26, 26)
        big = im.resize((fr.w * cell, fr.h * cell), Image.Resampling.NEAREST)
        self._photo_anim = ImageTk.PhotoImage(big)
        ox = (280 - big.width) // 2
        oy = (200 - big.height) // 2
        self.anim_canvas.create_image(ox, oy, anchor=tk.NW, image=self._photo_anim)
        self.anim_status.set(f"Frame {idx + 1}/{len(self.anim_frames)}  ·  {fr.w}×{fr.h}")

    def anim_play_toggle(self) -> None:
        if self.anim_playing:
            self.anim_playing = False
            if self._anim_job is not None:
                try:
                    self.after_cancel(self._anim_job)
                except Exception:
                    pass
                self._anim_job = None
            self._set_status("Animation: stop")
            return
        if len(self.anim_frames) < 1:
            messagebox.showinfo("Animation", "Add frames first.")
            return
        self.anim_playing = True
        self._anim_idx = 0
        self._anim_tick()

    def _anim_tick(self) -> None:
        if not self.anim_playing or not self.anim_frames:
            self.anim_playing = False
            return
        self.anim_list.selection_clear(0, tk.END)
        self.anim_list.selection_set(self._anim_idx)
        self._anim_preview_frame(self._anim_idx)
        self._anim_idx = (self._anim_idx + 1) % len(self.anim_frames)
        try:
            delay = max(50, int(self.anim_delay.get()))
        except (tk.TclError, ValueError):
            delay = 200
        self._anim_job = self.after(delay, self._anim_tick)

    def anim_save(self) -> None:
        if len(self.anim_frames) < 2:
            messagebox.showwarning("Animation", "At least 2 frames required.")
            return
        w, h = self._anim_wh()
        for i, fr in enumerate(self.anim_frames):
            if fr.w != w or fr.h != h:
                nb = PixelBitmap(w, h)
                nb.from_pil(fr.to_pil().resize((w, h), Image.Resampling.NEAREST))
                self.anim_frames[i] = nb
        name = sanitize(self.out_name.get()) or "anim"
        self.out_name.set(name)
        try:
            delay = max(50, int(self.anim_delay.get()))
        except (tk.TclError, ValueError):
            delay = 200
            self.anim_delay.set(delay)
        READY.mkdir(parents=True, exist_ok=True)
        frames_dir = READY / f"{name}_{w}x{h}_anim"
        frames_dir.mkdir(exist_ok=True)
        packed: list[list[int]] = []
        for i, fr in enumerate(self.anim_frames):
            mono = fr.to_pil()
            mono.save(frames_dir / f"frame_{i:02d}.png")
            packed.append(pack_bytes(mono))
        strip = Image.new("1", (w * len(self.anim_frames), h), 1)
        for i, fr in enumerate(self.anim_frames):
            strip.paste(fr.to_pil(), (i * w, 0))
        strip.save(READY / f"{name}_{w}x{h}_anim_strip.png")
        c_path = READY / f"{name}_{w}x{h}_anim.c"
        c_path.write_text(c_anim(name, w, h, packed, delay), encoding="utf-8")
        h_snip = (
            f"/* Include in firmware:\n"
            f" *   #include \"icons.h\"\n"
            f" *   // link {name}_{w}x{h}_anim.c\n"
            f" *   extern const icon_anim_t anim_{name}_{w}x{h};\n"
            f" *   icon_anim_draw(fb, x, y, &anim_{name}_{w}x{h}, now_ms, 0);\n"
            f" */\n"
        )
        (READY / f"{name}_{w}x{h}_anim.h").write_text(
            h_snip + f"\n#include \"icons.h\"\n\nextern const icon_anim_t anim_{name}_{w}x{h};\n",
            encoding="utf-8",
        )
        meta = {
            "name": name,
            "w": w,
            "h": h,
            "frame_ms": delay,
            "frames": len(self.anim_frames),
            "c_symbol": f"anim_{name}_{w}x{h}",
            "api": f"icon_anim_draw(fb, x, y, &anim_{name}_{w}x{h}, now_ms, 0)",
        }
        (READY / f"{name}_{w}x{h}_anim.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._set_status(f"Animation: {c_path.name} ({len(self.anim_frames)} frames)")
        if messagebox.askyesno(
            "Animation saved",
            f"{len(self.anim_frames)} frames → {c_path.name}\n"
            f"Symbol: anim_{name}_{w}x{h}\n"
            f"API: icon_anim_draw(...)\n\nOpen output folder?",
        ):
            self.open_ready()


    def _num_param(self, parent, label, var, lo, hi, cmd, fmt: str) -> None:
        """Slider + editable entry for any numeric layer / global param."""
        fr = ttk.Frame(parent)
        fr.pack(fill=tk.X, pady=1)
        ttk.Label(fr, text=label, width=8).pack(side=tk.LEFT)
        ent = ttk.Entry(fr, width=7)
        ent.pack(side=tk.RIGHT, padx=(4, 0))

        def fmt_val() -> str:
            try:
                return fmt.format(float(var.get()))
            except (tk.TclError, ValueError):
                return ""

        def push_entry() -> None:
            ent.delete(0, tk.END)
            ent.insert(0, fmt_val())

        def from_scale(_v=None) -> None:
            if getattr(self, "_loading_params", False):
                push_entry()
                return
            push_entry()
            if self._ui_ready and not self._loading_params and cmd:
                cmd()

        def from_entry(_e=None) -> None:
            raw = ent.get().strip().replace(",", ".")
            try:
                v = float(raw)
            except ValueError:
                push_entry()
                return
            self._loading_params = True
            try:
                if isinstance(var, tk.IntVar):
                    var.set(int(round(v)))
                else:
                    var.set(v)
            finally:
                self._loading_params = False
            push_entry()
            if self._ui_ready and cmd:
                cmd()

        ttk.Scale(fr, from_=lo, to=hi, variable=var, command=from_scale).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ent.bind("<Return>", from_entry)
        ent.bind("<FocusOut>", from_entry)
        push_entry()
        # keep entry in sync when var changes elsewhere (load / mouse drag)
        def on_var(*_a):
            if self._loading_params:
                return
            # avoid fighting while typing
            if self.focus_get() is ent:
                return
            push_entry()
        try:
            var.trace_add("write", on_var)
        except AttributeError:
            var.trace("w", on_var)

    def _sync_comp_cursor(self) -> None:
        mode = self.comp_mode.get()
        self.canvas_comp.configure(cursor="crosshair" if mode == "crop" else "fleur")

    def _comp_xy(self, e) -> tuple[int, int]:
        s = getattr(self, "_comp_scale", 1.0) or 1.0
        return int(e.x / s), int(e.y / s)


    def _set_status(self, t: str) -> None:
        self.status.set(t)

    def apply_custom_size(self) -> None:
        """Resize the custom icon canvas from manual WxH fields."""
        try:
            w = int(str(self.custom_w.get()).strip())
            h = int(str(self.custom_h.get()).strip())
        except ValueError:
            messagebox.showerror("Size", f"Enter integer W and H (1–{CUSTOM_MAX}).")
            return
        if w < 1 or h < 1 or w > CUSTOM_MAX or h > CUSTOM_MAX:
            messagebox.showerror("Size", f"Range 1…{CUSTOM_MAX} for each side.")
            return
        self.bmp_custom.resize(w, h, keep=True)
        self.custom_w.set(str(w))
        self.custom_h.set(str(h))
        if hasattr(self, "cv_custom"):
            self.cv_custom.reconfigure(cell_for_size(w, h))
        if hasattr(self, "pix_nb") and hasattr(self, "cv_custom"):
            try:
                idx = self.pix_nb.index(self.cv_custom.master.master)
                self.pix_nb.tab(idx, text=f"Custom {w}×{h}")
                self.pix_nb.select(idx)
            except Exception:
                pass
        self.edit_target.set("custom")
        self.size_mode.set("custom")
        self.auto_from_layers.set(False)
        self._set_status(f"Custom canvas: {w}×{h}")

    def _active_bitmaps(self) -> list[PixelBitmap]:
        t = self.edit_target.get()
        if t == "16":
            return [self.bmp16]
        if t == "11":
            return [self.bmp11]
        if t == "6":
            return [self.bmp6]
        if t == "22":
            return [self.bmp22]
        if t == "custom":
            return [self.bmp_custom]
        if t == "84":
            return [self.bmp84]
        if t == "72":
            return [self.bmp72]
        return [
            self.bmp16, self.bmp11, self.bmp6, self.bmp22,
            self.bmp_custom, self.bmp84, self.bmp72,
        ]

    def _redraw_editors(self) -> None:
        self.cv16.redraw()
        self.cv11.redraw()
        self.cv6.redraw()
        self.cv22.redraw()
        if hasattr(self, "cv_custom"):
            self.cv_custom.redraw()
        self.cv84.redraw()
        self.cv72.redraw()

    def _on_pixels_edited(self) -> None:
        # manual edit disables auto overwrite
        if self.auto_from_layers.get():
            self.auto_from_layers.set(False)
            self._set_status("Auto-update off — manual edit")
        else:
            self._set_status("Pixels updated")

    def _on_auto_toggle(self) -> None:
        if self.auto_from_layers.get():
            self.pull_from_layers()

    def _on_threshold(self, *_a) -> None:
        self._refresh_comp()
        if self.auto_from_layers.get():
            self.pull_from_layers()

    def edit_undo(self) -> None:
        ok = False
        for b in self._active_bitmaps():
            ok = b.undo() or ok
        self._redraw_editors()
        if ok:
            self.auto_from_layers.set(False)
            self._set_status("Undo")

    def edit_redo(self) -> None:
        ok = False
        for b in self._active_bitmaps():
            ok = b.redo() or ok
        self._redraw_editors()
        if ok:
            self.auto_from_layers.set(False)

    def _edit_op(self, fn) -> None:
        for b in self._active_bitmaps():
            b.push_undo()
            fn(b)
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("Pixels updated")

    def edit_clear(self) -> None:
        self._edit_op(lambda b: b.clear(False))

    def edit_invert(self) -> None:
        self._edit_op(lambda b: b.invert())

    def edit_flip_h(self) -> None:
        self._edit_op(lambda b: b.flip_h())

    def edit_flip_v(self) -> None:
        self._edit_op(lambda b: b.flip_v())

    def edit_rot(self) -> None:
        self._edit_op(lambda b: b.rotate_cw())

    def edit_shift(self, dx: int, dy: int) -> None:
        self._edit_op(lambda b: b.shift(dx, dy))

    def edit_dilate(self) -> None:
        self._edit_op(lambda b: b.dilate())

    def edit_erode(self) -> None:
        self._edit_op(lambda b: b.erode())

    def edit_crop_content(self) -> None:
        def op(b: PixelBitmap) -> None:
            box = b.content_bbox()
            if box:
                b.keep_rect(*box)
        self._edit_op(op)
        self._set_status("Pixels cropped to content")

    def edit_scale_16_to_11(self) -> None:
        self.bmp11.push_undo()
        small = self.bmp16.to_pil().resize((11, 7), Image.Resampling.NEAREST)
        self.bmp11.from_pil(small)
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("16×16 downscaled to 11×7")

    def edit_scale_11_to_16(self) -> None:
        self.bmp16.push_undo()
        big = self.bmp11.to_pil().resize((16, 16), Image.Resampling.NEAREST)
        self.bmp16.from_pil(big)
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("11×7 upscaled to 16×16")

    def edit_copy_16_to_11(self) -> None:
        self.bmp11.push_undo()
        self.bmp11.clear(False)
        ox, oy = (11 - 11) // 2, (7 - 7) // 2
        # center-crop 11×7 from middle of 16×16
        sx, sy = (16 - 11) // 2, (16 - 7) // 2
        for y in range(7):
            for x in range(11):
                self.bmp11.set(x + ox, y + oy, self.bmp16.get(sx + x, sy + y))
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("Center of 16×16 copied to 11×7")

    def edit_scale_to_84(self) -> None:
        """Scale current edit target (or 16×16) into full-screen 84×48."""
        src = self.bmp16
        t = self.edit_target.get()
        if t == "11":
            src = self.bmp11
        elif t == "72":
            src = self.bmp72
        elif t == "84":
            src = self.bmp84
        self.bmp84.push_undo()
        self.bmp84.from_pil(src.to_pil().resize((84, 48), Image.Resampling.NEAREST))
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("Scaled to 84×48")

    def edit_scale_to_72(self) -> None:
        src = self.bmp16
        t = self.edit_target.get()
        if t == "11":
            src = self.bmp11
        elif t == "84":
            src = self.bmp84
        elif t == "72":
            src = self.bmp72
        self.bmp72.push_undo()
        self.bmp72.from_pil(src.to_pil().resize((72, 28), Image.Resampling.NEAREST))
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("Scaled to 72×28")

    def edit_scale_84_to_72(self) -> None:
        self.bmp72.push_undo()
        self.bmp72.from_pil(self.bmp84.to_pil().resize((72, 28), Image.Resampling.NEAREST))
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("84×48 → 72×28")

    def edit_scale_72_to_84(self) -> None:
        self.bmp84.push_undo()
        self.bmp84.from_pil(self.bmp72.to_pil().resize((84, 48), Image.Resampling.NEAREST))
        self.auto_from_layers.set(False)
        self._redraw_editors()
        self._set_status("72×28 → 84×48")

    def pull_from_layers(self) -> None:
        if not self.layers:
            self.bmp16.clear(False)
            self.bmp11.clear(False)
            self.bmp6.clear(False)
            self.bmp22.clear(False)
            self.bmp_custom.clear(False)
            self.bmp84.clear(False)
            self.bmp72.clear(False)
        else:
            self.bmp16.from_pil(self._render_size(16, 16))
            self.bmp11.from_pil(self._render_size(11, 7))
            self.bmp6.from_pil(self._render_size(6, 7))
            self.bmp22.from_pil(self._render_size(22, 26))
            self.bmp_custom.from_pil(self._render_size(self.bmp_custom.w, self.bmp_custom.h))
            self.bmp84.from_pil(self._render_size(84, 48))
            self.bmp72.from_pil(self._render_size(72, 28))
        self._redraw_editors()
        self._set_status("Pixels refreshed from layers")

    def open_ready(self) -> None:
        READY.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(READY))
        except OSError as e:
            messagebox.showerror("Error", str(e))

    def show_help(self) -> None:
        messagebox.showinfo(
            "Help",
            "Ctrl+V — paste screenshot or image from clipboard.\n"
            "Ctrl+G — generate (new variants each time).\n"
            "Variants — how many icons to add per run.\n\n"
            "LMB on grid — draw; RMB — pick color.\n"
            "Uncheck Auto-refresh so layers do not overwrite edits.\n"
            "Save writes pixel canvases:\n"
            "16×16, 11×7, 6×7, 22×26, Custom (manual WxH), 84×48, 72×28.\n"
            "Tools: Pan / Crop.\n"
            "Ctrl+Z — undo. Animation tab — frames → C.\n\n"
            "From file / From folder (pixel tabs) loads\n"
            "icons with auto W×H (from image or icon_22x26.png).",
        )

    def _add_layer_image(self, im: Image.Image, name: str, select: bool = True) -> None:
        # Keep real pixel size (no forced downscale to 512)
        im = im.convert("RGBA")
        self.layers.append(Layer(Path(f"layer:{name}"), im, name))
        # 1:1 stamp into matching pixel canvas when size matches exactly
        self._stamp_native_if_match(im)
        if select:
            self._refresh_list()
            self.sel.set(len(self.layers) - 1)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(self.sel.get())
            self.listbox.see(self.sel.get())
            self._load_sel_params()

    def _bitmap_for_wh(self, w: int, h: int) -> tuple[PixelBitmap, str, str]:
        """Return (bitmap, edit_target, size_mode) for WxH."""
        preset = ICON_CANVAS_PRESETS.get((w, h))
        if preset:
            target, mode = preset
            bmp = {
                "16": self.bmp16, "11": self.bmp11, "6": self.bmp6,
                "22": self.bmp22, "84": self.bmp84, "72": self.bmp72,
            }[target]
            return bmp, target, mode
        if (w, h) == (self.bmp_custom.w, self.bmp_custom.h):
            return self.bmp_custom, "custom", "custom"
        self.bmp_custom.resize(w, h, keep=False)
        self.custom_w.set(str(w))
        self.custom_h.set(str(h))
        if hasattr(self, "cv_custom"):
            self.cv_custom.reconfigure(cell_for_size(w, h))
        if hasattr(self, "pix_nb") and hasattr(self, "cv_custom"):
            try:
                idx = self.pix_nb.index(self.cv_custom.master.master)
                self.pix_nb.tab(idx, text=f"Custom {w}×{h}")
            except Exception:
                pass
        return self.bmp_custom, "custom", "custom"

    def _focus_canvas(self, target: str) -> None:
        """Select pixel-editor tab and save target for transforms."""
        cv_map = {
            "16": getattr(self, "cv16", None),
            "11": getattr(self, "cv11", None),
            "6": getattr(self, "cv6", None),
            "22": getattr(self, "cv22", None),
            "custom": getattr(self, "cv_custom", None),
            "84": getattr(self, "cv84", None),
            "72": getattr(self, "cv72", None),
        }
        cv = cv_map.get(target)
        if cv is not None and hasattr(self, "pix_nb"):
            try:
                self.pix_nb.select(self.pix_nb.index(cv.master.master))
            except Exception:
                pass
        self.edit_target.set(target)

    def _load_icon_mono_to_canvas(
        self, path: Path, im: Image.Image, *, focus: bool = True
    ) -> tuple[int, int, str] | None:
        """Load one image into the matching icon tab. Returns (w, h, source) or None."""
        w, h, resize, source = resolve_icon_wh(path, im)
        if w < 1 or h < 1 or w > CUSTOM_MAX or h > CUSTOM_MAX:
            return None
        mono = to_mono(im.convert("RGBA"), int(self.threshold.get()))
        if self.invert.get():
            mono = mono.point(lambda p: 255 - p)
        if resize:
            mono = mono.resize((w, h), Image.Resampling.NEAREST)
        bmp, target, mode = self._bitmap_for_wh(w, h)
        bmp.push_undo()
        bmp.from_pil(mono)
        self.size_mode.set(mode)
        if focus:
            self._focus_canvas(target)
        return w, h, source

    def _stamp_native_if_match(self, im: Image.Image) -> None:
        """If pasted image matches a canvas size, write pixels 1:1 (no rescale)."""
        w, h = im.size
        if (w, h) not in ICON_CANVAS_PRESETS and (w, h) != (self.bmp_custom.w, self.bmp_custom.h):
            return
        if self._load_icon_mono_to_canvas(Path("paste"), im, focus=False):
            self.auto_from_layers.set(False)
            self._redraw_editors()

    def import_icon_file(self) -> None:
        """Load icon(s) from disk into the matching pixel tab (auto WxH)."""
        paths = filedialog.askopenfilenames(
            title="Icon → tab by size",
            initialdir=str(READY),
            filetypes=[
                ("Images", "*.png;*.bmp;*.gif;*.jpg;*.jpeg;*.webp"),
                ("PNG", "*.png"),
                ("All", "*.*"),
            ],
        )
        if not paths:
            return
        loaded: list[str] = []
        last_wh: tuple[int, int] | None = None
        for p in paths:
            path = Path(p)
            try:
                im = Image.open(path).convert("RGBA")
            except Exception as e:
                messagebox.showerror("Error", f"{path.name}: {e}")
                continue
            info = self._load_icon_mono_to_canvas(path, im, focus=(p == paths[-1]))
            if not info:
                messagebox.showwarning("Size", f"{path.name}: invalid resolution.")
                continue
            w, h, source = info
            last_wh = (w, h)
            loaded.append(f"{path.name} → {w}×{h} ({source})")
            stem_raw = re.sub(r"[_-]?\d+x\d+$", "", path.stem, flags=re.I)
            stem = sanitize(stem_raw) or sanitize(path.stem)
            if stem and self.out_name.get() in ("", "my_icon"):
                self.out_name.set(stem)
        if not loaded:
            return
        self.auto_from_layers.set(False)
        self._redraw_editors()
        if last_wh:
            self._set_status(f"Loaded: {len(loaded)} — active {last_wh[0]}×{last_wh[1]}")
        if len(loaded) == 1:
            self._set_status(loaded[0])

    def import_icon_directory(self) -> None:
        """Load all images from a folder; each file goes to its auto-detected tab."""
        folder = filedialog.askdirectory(
            title="Folder with icons",
            initialdir=str(READY),
        )
        if not folder:
            return
        files = iter_image_files(Path(folder))
        if not files:
            messagebox.showinfo("Folder", "No PNG/BMP/GIF/JPG/WebP in the selected folder.")
            return
        loaded: list[str] = []
        sizes: set[tuple[int, int]] = set()
        for i, path in enumerate(files):
            try:
                im = Image.open(path).convert("RGBA")
            except Exception:
                continue
            info = self._load_icon_mono_to_canvas(
                path, im, focus=(i == len(files) - 1)
            )
            if not info:
                continue
            w, h, source = info
            sizes.add((w, h))
            loaded.append(f"{path.name} ({w}×{h}, {source})")
        if not loaded:
            messagebox.showwarning("Folder", "Could not load any icons.")
            return
        self.auto_from_layers.set(False)
        self._redraw_editors()
        sz = ", ".join(f"{w}×{h}" for w, h in sorted(sizes))
        self._set_status(f"From folder: {len(loaded)} icon(s) → {sz}")

    def paste_clipboard(self) -> None:
        try:
            clip = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("Clipboard", f"Could not read clipboard:\n{e}")
            return
        if clip is None:
            messagebox.showinfo(
                "Clipboard empty",
                "Copy a screenshot (Win+Shift+S / PrintScreen)\n"
                "or an image, then press Ctrl+V.",
            )
            return
        # grabclipboard may return a list of file paths
        if isinstance(clip, list):
            added = 0
            for item in clip:
                path = Path(str(item))
                if not path.is_file():
                    continue
                try:
                    im = Image.open(path).convert("RGBA")
                except Exception:
                    continue
                self._gen_serial += 1
                self._add_layer_image(im, f"paste_{self._gen_serial}", select=False)
                added += 1
            if not added:
                messagebox.showinfo("Clipboard", "No image in clipboard.")
                return
            self._refresh_list()
            self.sel.set(len(self.layers) - 1)
            self.listbox.selection_set(self.sel.get())
            self._load_sel_params()
            self._refresh_comp()
            if self.auto_from_layers.get():
                self.pull_from_layers()
            else:
                self._redraw_editors()
            self._set_status(f"Pasted {added} file(s) from clipboard (native size)")
            return
        if not isinstance(clip, Image.Image):
            messagebox.showinfo("Clipboard", "No image in clipboard.")
            return
        self._gen_serial += 1
        name = f"screenshot_{self._gen_serial}"
        self._add_layer_image(clip.convert("RGBA"), name)
        if self.out_name.get() in ("", "my_icon"):
            self.out_name.set(name)
        self._refresh_comp()
        if self.auto_from_layers.get():
            self.pull_from_layers()
        else:
            self._redraw_editors()
        w, h = clip.size if isinstance(clip, Image.Image) else (0, 0)
        self._set_status(f'Pasted {w}×{h} → layer "{name}" (1:1)')

    def generate_from_prompt(self) -> None:
        text = self.prompt.get().strip()
        if not text:
            messagebox.showinfo("Prompt", "Try: key, phone, envelope…")
            return
        try:
            count = max(1, min(12, int(self.gen_count.get())))
        except (tk.TclError, ValueError):
            count = 1
        kind = detect_prompt_kind(text)
        base = sanitize(text)[:20] or kind
        first_idx = len(self.layers)
        for i in range(count):
            self._gen_serial += 1
            seed = time.time_ns() ^ (self._gen_serial * 0x9E3779B9) ^ (i * 0x85EBCA6B)
            im = generate_icon_from_prompt(text, 128, seed=seed)
            lname = f"gen_{base}_{self._gen_serial}"
            self._add_layer_image(im, lname, select=False)
        self._refresh_list()
        self.sel.set(first_idx)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(first_idx)
        self.listbox.see(len(self.layers) - 1)
        self._load_sel_params()
        if self.out_name.get() in ("", "my_icon"):
            self.out_name.set(base if base != "icon" else kind)
        self.auto_from_layers.set(True)
        self._refresh_all()
        self._set_status(f"Generated {count} {kind} variant(s) (run again for more)")

    def import_images(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp"), ("All", "*.*")]
        )
        if not paths:
            return
        added = 0
        for p in paths:
            path = Path(p)
            try:
                im = Image.open(path).convert("RGBA")
            except Exception as e:
                messagebox.showerror("Error", f"{path.name}: {e}")
                continue
            # Keep native resolution; 1:1 stamp if size matches a canvas
            self.layers.append(Layer(path, im, path.stem))
            self._stamp_native_if_match(im)
            added += 1
        if added and self.out_name.get() in ("", "my_icon"):
            self.out_name.set(sanitize(Path(paths[0]).stem))
        self._refresh_list()
        if self.layers:
            self.sel.set(max(0, len(self.layers) - added))
            self.listbox.selection_set(self.sel.get())
            self._load_sel_params()
        # If we stamped native sizes, auto is already off; else refresh from layers
        if self.auto_from_layers.get():
            self._refresh_all()
        else:
            self._refresh_comp()
            self._redraw_editors()
        self._set_status(f"Import: +{added} (native size)")

    def remove_layer(self) -> None:
        i = self._cur_index()
        if i < 0:
            return
        self.layers.pop(i)
        self.sel.set(min(i, len(self.layers) - 1) if self.layers else -1)
        self._refresh_list()
        if self.layers:
            self.listbox.selection_set(self.sel.get())
            self._load_sel_params()
        self._refresh_all()

    def move_layer(self, delta: int) -> None:
        i = self._cur_index()
        j = i + delta
        if i < 0 or j < 0 or j >= len(self.layers):
            return
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        self.sel.set(j)
        self._refresh_list()
        self.listbox.selection_set(j)
        self._refresh_all()

    def _cur_index(self) -> int:
        sel = self.listbox.curselection()
        return int(sel[0]) if sel else int(self.sel.get())

    def _on_select(self, _e=None) -> None:
        self.sel.set(self._cur_index())
        self._load_sel_params()

    def _load_sel_params(self) -> None:
        i = int(self.sel.get())
        if i < 0 or i >= len(self.layers):
            return
        L = self.layers[i]
        self._loading_params = True
        try:
            self.ang.set(L.angle)
            self.con.set(L.contrast)
            self.ox.set(float(L.ox))
            self.oy.set(float(L.oy))
            self.sc.set(L.scale)
            self.layer_visible.set(L.visible)
            self.crop_x.set(0)
            self.crop_y.set(0)
            self.crop_w.set(L.image.width)
            self.crop_h.set(L.image.height)
        finally:
            self._loading_params = False

    def _on_visibility(self) -> None:
        i = int(self.sel.get())
        if 0 <= i < len(self.layers):
            self.layers[i].visible = bool(self.layer_visible.get())
            self._refresh_list()
            self._refresh_all()

    def _on_layer_param(self, *_a) -> None:
        i = int(self.sel.get())
        if i < 0 or i >= len(self.layers):
            return
        L = self.layers[i]
        L.angle = float(self.ang.get())
        L.contrast = float(self.con.get())
        L.ox = int(round(float(self.ox.get())))
        L.oy = int(round(float(self.oy.get())))
        L.scale = float(self.sc.get())
        self._refresh_all()

    def _layer_transformed(self, L: Layer) -> Image.Image:
        im = L.image.copy()
        rgb = ImageEnhance.Contrast(im.convert("RGB")).enhance(L.contrast)
        a = im.split()[-1] if im.mode == "RGBA" else Image.new("L", im.size, 255)
        im = Image.merge("RGBA", (*rgb.split(), a))
        if abs(L.scale - 1.0) > 0.001:
            im = im.resize(
                (max(1, int(im.width * L.scale)), max(1, int(im.height * L.scale))),
                Image.Resampling.NEAREST if max(im.size) <= 256 else Image.Resampling.LANCZOS,
            )
        if abs(L.angle) > 0.01:
            im = im.rotate(L.angle, expand=True, resample=Image.Resampling.NEAREST)
        return im

    def _layer_place(self, L: Layer, cw: int, ch: int) -> tuple[Image.Image, int, int]:
        im = self._layer_transformed(L)
        x = (cw - im.width) // 2 + L.ox
        y = (ch - im.height) // 2 + L.oy
        return im, x, y

    def _hit_layer(self, px: int, py: int) -> int:
        """Topmost visible layer under point on composition (list order = top first)."""
        for i, L in enumerate(self.layers):
            if not L.visible:
                continue
            im, x, y = self._layer_place(L, COMP_SIZE, COMP_SIZE)
            if x <= px < x + im.width and y <= py < y + im.height:
                # optional: check alpha
                ax, ay = px - x, py - y
                try:
                    if im.mode == "RGBA" and im.getpixel((ax, ay))[3] < 8:
                        continue
                except Exception:
                    pass
                return i
        return int(self.sel.get()) if self.layers else -1

    def _comp_down(self, e) -> None:
        if not self.layers:
            return
        cx, cy = self._comp_xy(e)
        mode = self.comp_mode.get()
        if mode == "crop":
            self._comp_drag = True
            self._comp_mx0, self._comp_my0 = cx, cy
            self._comp_crop_rect = None
            return
        i = self._hit_layer(cx, cy)
        if i < 0:
            return
        self.sel.set(i)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self._load_sel_params()
        L = self.layers[i]
        self._comp_drag = True
        self._comp_mx0, self._comp_my0 = cx, cy
        self._comp_ox0, self._comp_oy0 = L.ox, L.oy

    def _comp_move(self, e) -> None:
        if not self._comp_drag:
            return
        cx, cy = self._comp_xy(e)
        mode = self.comp_mode.get()
        if mode == "crop":
            self._comp_crop_rect = (self._comp_mx0, self._comp_my0, cx, cy)
            self._refresh_comp()
            return
        i = int(self.sel.get())
        if i < 0 or i >= len(self.layers):
            return
        L = self.layers[i]
        L.ox = self._comp_ox0 + (cx - self._comp_mx0)
        L.oy = self._comp_oy0 + (cy - self._comp_my0)
        self._loading_params = True
        try:
            self.ox.set(float(L.ox))
            self.oy.set(float(L.oy))
        finally:
            self._loading_params = False
        self._refresh_all()

    def _comp_up(self, e) -> None:
        if not self._comp_drag:
            return
        self._comp_drag = False
        cx, cy = self._comp_xy(e)
        mode = self.comp_mode.get()
        if mode == "crop":
            self._comp_crop_rect = None
            self._crop_layer_from_comp(self._comp_mx0, self._comp_my0, cx, cy)
            return
        self._set_status(f"Layer moved: X={int(self.ox.get())} Y={int(self.oy.get())}")

    def _crop_layer_from_comp(self, x0: int, y0: int, x1: int, y1: int) -> None:
        i = int(self.sel.get())
        if i < 0 or i >= len(self.layers):
            self._set_status("Select a layer to crop")
            self._refresh_comp()
            return
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        if xb - xa < 2 or yb - ya < 2:
            self._refresh_comp()
            return
        L = self.layers[i]
        # Crop in source image space: map compose rect → untransformed image
        # Approximate via transformed image then crop bitmap and replace layer
        im, lx, ly = self._layer_place(L, COMP_SIZE, COMP_SIZE)
        # intersection of crop rect with placed image
        cx0 = max(0, xa - lx)
        cy0 = max(0, ya - ly)
        cx1 = min(im.width, xb - lx)
        cy1 = min(im.height, yb - ly)
        if cx1 - cx0 < 1 or cy1 - cy0 < 1:
            self._set_status("Marquee does not intersect layer")
            self._refresh_comp()
            return
        cropped = im.crop((cx0, cy0, cx1, cy1))
        L.image = cropped.convert("RGBA")
        L.scale = 1.0
        L.angle = 0.0
        # keep visual position: new center should match cropped region center
        old_cx = lx + (cx0 + cx1) / 2.0
        old_cy = ly + (cy0 + cy1) / 2.0
        L.ox = int(round(old_cx - COMP_SIZE / 2.0))
        L.oy = int(round(old_cy - COMP_SIZE / 2.0))
        self._load_sel_params()
        self._refresh_all()
        self._set_status(f"Layer cropped → {L.image.width}×{L.image.height}")

    def crop_layer_box(self) -> None:
        i = int(self.sel.get())
        if i < 0 or i >= len(self.layers):
            messagebox.showinfo("Crop", "Select a layer.")
            return
        L = self.layers[i]
        try:
            x = int(self.crop_x.get())
            y = int(self.crop_y.get())
            w = int(self.crop_w.get())
            h = int(self.crop_h.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Crop", "Invalid X/Y/W/H.")
            return
        if w < 1 or h < 1:
            return
        box = (x, y, x + w, y + h)
        # clamp to image
        iw, ih = L.image.size
        box = (max(0, box[0]), max(0, box[1]), min(iw, box[2]), min(ih, box[3]))
        if box[2] <= box[0] or box[3] <= box[1]:
            messagebox.showerror("Crop", "Empty region.")
            return
        L.image = L.image.crop(box).convert("RGBA")
        L.scale = 1.0
        self._load_sel_params()
        self._refresh_all()
        self._set_status(f"Layer cropped → {L.image.width}×{L.image.height}")

    def crop_layer_content(self) -> None:
        i = int(self.sel.get())
        if i < 0 or i >= len(self.layers):
            messagebox.showinfo("Crop", "Select a layer.")
            return
        L = self.layers[i]
        im = L.image.convert("RGBA")
        bbox = im.getbbox()
        if not bbox:
            # try mono threshold bbox
            mono = to_mono(im, int(self.threshold.get()))
            # getbbox on mode 1: black=0 may not count; invert for bbox of ink
            inv = mono.point(lambda p: 255 - p)
            bbox = inv.convert("L").getbbox()
        if not bbox:
            self._set_status("No content to crop")
            return
        L.image = im.crop(bbox)
        L.scale = 1.0
        self._load_sel_params()
        self._refresh_all()
        self._set_status(f"Crop to content → {L.image.width}×{L.image.height}")

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for L in self.layers:
            self.listbox.insert(tk.END, ("" if L.visible else "[ ] ") + L.name)

    def _compose(self, cw: int, ch: int) -> Image.Image:
        """Compose layers at real pixel size — no forced fit/thumbnail."""
        base = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
        for L in reversed(self.layers):
            if not L.visible:
                continue
            im, x, y = self._layer_place(L, cw, ch)
            _safe_paste(base, im, x, y)
        return base

    def _render_size(self, w: int, h: int) -> Image.Image:
        # Exact target size (1:1 for matching icons); no 8× upsample mush
        comp = self._compose(w, h)
        mono = to_mono(comp, int(self.threshold.get()))
        if self.invert.get():
            mono = mono.point(lambda p: 255 - p)
        return mono

    def _refresh_comp(self) -> None:
        self.canvas_comp.delete("all")
        s = getattr(self, "_comp_scale", COMP_VIEW / COMP_SIZE)
        if not self.layers:
            self.canvas_comp.create_text(
                COMP_VIEW // 2, COMP_VIEW // 2, text="none", fill=THEME["muted"]
            )
            self._photo_comp = None
            return
        comp = self._compose(COMP_SIZE, COMP_SIZE)
        big = comp.convert("RGB").resize((COMP_VIEW, COMP_VIEW), Image.Resampling.NEAREST)
        self._photo_comp = ImageTk.PhotoImage(big)
        self.canvas_comp.create_image(0, 0, anchor=tk.NW, image=self._photo_comp)
        # selection outline
        i = int(self.sel.get())
        if 0 <= i < len(self.layers) and self.layers[i].visible:
            im, x, y = self._layer_place(self.layers[i], COMP_SIZE, COMP_SIZE)
            self.canvas_comp.create_rectangle(
                x * s, y * s, (x + im.width) * s, (y + im.height) * s,
                outline="#4fc3f7", width=2, dash=(3, 2),
            )
        if self._comp_crop_rect:
            a, b, c, d = self._comp_crop_rect
            self.canvas_comp.create_rectangle(
                a * s, b * s, c * s, d * s, outline="#ff7043", width=2, dash=(4, 2)
            )

    def _refresh_all(self) -> None:
        if not self._ui_ready:
            return
        self._refresh_comp()
        if self.auto_from_layers.get():
            self.pull_from_layers()
        else:
            self._redraw_editors()

    def save_icons(self) -> None:
        # Allow save even without layers if pixels exist
        has_ink = (
            any(any(row) for row in self.bmp16.data)
            or any(any(row) for row in self.bmp11.data)
            or any(any(row) for row in self.bmp6.data)
            or any(any(row) for row in self.bmp22.data)
            or any(any(row) for row in self.bmp_custom.data)
            or any(any(row) for row in self.bmp84.data)
            or any(any(row) for row in self.bmp72.data)
        )
        if not self.layers and not has_ink:
            messagebox.showwarning("Empty", "No layers and no drawn pixels.")
            return
        name = sanitize(self.out_name.get())
        self.out_name.set(name)
        mode = self.size_mode.get()
        targets: list[tuple[int, int, PixelBitmap]] = []
        if mode in ("both", "16", "all"):
            targets.append((16, 16, self.bmp16))
        if mode in ("both", "11", "all"):
            targets.append((11, 7, self.bmp11))
        if mode in ("84", "all"):
            targets.append((84, 48, self.bmp84))
        if mode in ("72", "all"):
            targets.append((72, 28, self.bmp72))
        if mode in ("6", "all"):
            targets.append((6, 7, self.bmp6))
        if mode in ("22", "all"):
            targets.append((22, 26, self.bmp22))
        if mode == "custom":
            targets.append((self.bmp_custom.w, self.bmp_custom.h, self.bmp_custom))
        elif mode == "all":
            presets = {(16, 16), (11, 7), (6, 7), (22, 26), (72, 28), (84, 48)}
            key = (self.bmp_custom.w, self.bmp_custom.h)
            if key not in presets:
                targets.append((key[0], key[1], self.bmp_custom))

        READY.mkdir(parents=True, exist_ok=True)
        saved = []
        for w, h, bmp in targets:
            mono = bmp.to_pil()
            stem = f"{name}_{w}x{h}"
            mono.save(READY / f"{stem}.png")
            (READY / f"{stem}.txt").write_text("\n".join(ascii_rows(mono)) + "\n", encoding="utf-8")
            (READY / f"{stem}.c").write_text(c_array(name, w, h, pack_bytes(mono)), encoding="utf-8")
            saved.append(stem)

        man_path = READY / "manifest.json"
        manifest = []
        if man_path.exists():
            try:
                manifest = json.loads(man_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = []
        manifest = [m for m in manifest if m.get("name") != name]
        manifest.append({"name": name, "files": saved})
        man_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        self._set_status(f"Saved: {', '.join(saved)}")
        if messagebox.askyesno("Saved", f"{name}\nOpen output folder?"):
            self.open_ready()


def main() -> None:
    IconStudio().mainloop()


if __name__ == "__main__":
    main()
