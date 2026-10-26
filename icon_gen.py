"""Procedural icon silhouettes with seed-based variants."""
from __future__ import annotations

import math
import random
import re
import time

from PIL import Image, ImageDraw

_PROMPT_MAP = [
    (("phone", "handset", "call"), "phone"),
    (("key", "lock", "keylock"), "key"),
    (("sms", "envelope", "mail", "message"), "envelope"),
    (("battery", "charge", "power"), "battery"),
    (("signal", "antenna", "bars", "network"), "signal"),
    (("bluetooth", "bt"), "bluetooth"),
    (("clock", "time", "alarm"), "clock"),
    (("game", "snake"), "game"),
    (("book", "contact", "phonebook"), "book"),
    (("setting", "gear", "cog"), "gear"),
    (("profile", "user", "person"), "person"),
    (("music", "note"), "music"),
    (("wifi", "wi-fi", "wlan"), "wifi"),
    (("star",), "star"),
    (("heart",), "heart"),
    (("home", "house"), "home"),
    (("camera", "photo"), "camera"),
    (("bell", "notification"), "bell"),
]


def detect_prompt_kind(prompt: str) -> str:
    p = prompt.strip().lower()
    for keys, kind in _PROMPT_MAP:
        if any(k in p for k in keys):
            return kind
    return "generic"


def generate_icon_from_prompt(prompt: str, size: int = 128, seed: int | None = None) -> Image.Image:
    """Different seed → different silhouette variant of the same kind."""
    rng = random.Random(seed if seed is not None else time.time_ns())
    kind = detect_prompt_kind(prompt)
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    ink = (0, 0, 0, 255)
    paper = (255, 255, 255, 255)
    m = max(2, size // 16)
    thick = rng.choice([max(2, size // 40), max(3, size // 28), max(4, size // 22)])
    margin = rng.uniform(0.08, 0.18)
    stretch = rng.uniform(0.88, 1.12)
    style = rng.randint(0, 3)

    def box(a, b, c, e, fill=ink):
        d.rectangle([a, b, c, e], fill=fill)

    def oval(a, b, c, e, fill=ink):
        d.ellipse([a, b, c, e], fill=fill)

    if kind == "phone":
        if style == 0:
            x0, x1 = size * (0.25 + margin * 0.2), size * (0.75 - margin * 0.2)
            box(x0, size * margin, x1, size * (1 - margin))
            d.rectangle(
                [x0 + size * 0.06, size * (margin + 0.06), x1 - size * 0.06, size * 0.42],
                fill=paper,
            )
            cols, rows = rng.choice([(2, 3), (3, 3), (3, 4)])
            for row in range(rows):
                for col in range(cols):
                    cx = x0 + (x1 - x0) * (0.2 + col * (0.6 / max(1, cols - 1)))
                    cy = size * (0.50 + row * 0.10)
                    r = m * rng.uniform(0.7, 1.2)
                    oval(cx - r, cy - r, cx + r, cy + r)
        elif style == 1:
            box(size * 0.22, size * 0.12, size * 0.78, size * 0.55)
            d.rectangle([size * 0.30, size * 0.18, size * 0.70, size * 0.48], fill=paper)
            box(size * 0.28, size * 0.55, size * 0.72, size * 0.90)
        else:
            oval(size * 0.15, size * 0.20, size * 0.40, size * 0.55)
            oval(size * 0.60, size * 0.45, size * 0.85, size * 0.80)
            d.line([(size * 0.35, size * 0.35), (size * 0.70, size * 0.60)], fill=ink, width=thick + 2)
    elif kind == "key":
        bow_r = size * rng.uniform(0.16, 0.22)
        bow_cx = size * rng.uniform(0.28, 0.36)
        bow_cy = size * 0.50
        oval(bow_cx - bow_r, bow_cy - bow_r, bow_cx + bow_r, bow_cy + bow_r)
        hole = bow_r * rng.uniform(0.35, 0.55)
        d.ellipse([bow_cx - hole, bow_cy - hole, bow_cx + hole, bow_cy + hole], fill=paper)
        shaft_y0 = bow_cy - size * rng.uniform(0.05, 0.09)
        shaft_y1 = bow_cy + size * rng.uniform(0.05, 0.09)
        shaft_x1 = size * rng.uniform(0.82, 0.92)
        box(bow_cx, shaft_y0, shaft_x1, shaft_y1)
        for i in range(rng.randint(1, 3)):
            tx = shaft_x1 - size * (0.06 + i * 0.08)
            th = size * rng.uniform(0.12, 0.22)
            tw = size * rng.uniform(0.05, 0.09)
            box(tx, shaft_y1, tx + tw, shaft_y1 + th)
    elif kind == "envelope":
        x0, y0 = size * margin, size * (0.22 + style * 0.02)
        x1, y1 = size * (1 - margin), size * (0.78 - style * 0.02)
        if style % 2 == 0:
            box(x0, y0, x1, y1)
            for t in range(thick):
                d.line(
                    [(x0, y0 + t), ((x0 + x1) / 2, (y0 + y1) / 2 + t), (x1, y0 + t)],
                    fill=ink,
                    width=1,
                )
        else:
            d.polygon(
                [(x0, y0 + size * 0.12), ((x0 + x1) / 2, y0 - size * 0.02), (x1, y0 + size * 0.12)],
                fill=ink,
            )
            box(x0, y0 + size * 0.10, x1, y1)
            d.rectangle(
                [x0 + size * 0.04, y0 + size * 0.18, x1 - size * 0.04, y1 - size * 0.06],
                fill=paper,
            )
    elif kind == "battery":
        fill_lvl = rng.uniform(0.25, 0.95)
        box(size * 0.20, size * 0.28, size * 0.80, size * 0.78)
        box(size * 0.38, size * 0.16, size * 0.62, size * 0.28)
        d.rectangle([size * 0.26, size * 0.34, size * 0.74, size * 0.72], fill=paper)
        box(size * 0.30, size * 0.38, size * 0.30 + size * 0.40 * fill_lvl, size * 0.68)
    elif kind == "signal":
        n = rng.randint(3, 5)
        for i in range(n):
            hfrac = 0.25 + (i + 1) / n * 0.65
            x0 = size * (0.15 + i * (0.70 / n))
            wbar = size * rng.uniform(0.08, 0.12)
            box(x0, size * 0.88 - size * hfrac, x0 + wbar, size * 0.88)
    elif kind == "bluetooth":
        cx = size // 2 + rng.randint(-4, 4)
        cy = size // 2
        s = size * rng.uniform(0.24, 0.32)
        pts = [
            (cx, cy - s), (cx + s * 0.9, cy - s * 0.35), (cx, cy),
            (cx + s * 0.9, cy + s * 0.35), (cx, cy + s), (cx, cy),
            (cx - s * 0.9, cy + s * 0.35), (cx, cy), (cx - s * 0.9, cy - s * 0.35), (cx, cy - s),
        ]
        d.line(pts, fill=ink, width=thick)
    elif kind == "clock":
        oval(size * margin, size * margin, size * (1 - margin), size * (1 - margin))
        d.ellipse(
            [
                size * (margin + 0.08),
                size * (margin + 0.08),
                size * (1 - margin - 0.08),
                size * (1 - margin - 0.08),
            ],
            fill=paper,
        )
        cx = cy = size // 2
        ang_h = rng.uniform(-math.pi, math.pi)
        ang_m = rng.uniform(-math.pi, math.pi)
        d.line(
            [(cx, cy), (cx + size * 0.18 * math.cos(ang_h), cy + size * 0.18 * math.sin(ang_h))],
            fill=ink,
            width=thick,
        )
        d.line(
            [(cx, cy), (cx + size * 0.28 * math.cos(ang_m), cy + size * 0.28 * math.sin(ang_m))],
            fill=ink,
            width=max(2, thick - 1),
        )
    elif kind == "game":
        if style <= 1:
            box(size * 0.15, size * 0.40, size * 0.50, size * 0.60)
            box(size * 0.25, size * 0.28, size * 0.40, size * 0.72)
            oval(size * 0.55, size * 0.30, size * 0.72, size * 0.47)
            oval(size * 0.70, size * 0.48, size * 0.88, size * 0.66)
        else:
            box(size * 0.12, size * 0.25, size * 0.88, size * 0.80)
            d.rectangle([size * 0.22, size * 0.32, size * 0.78, size * 0.55], fill=paper)
            oval(size * 0.25, size * 0.60, size * 0.40, size * 0.75)
            oval(size * 0.60, size * 0.60, size * 0.75, size * 0.75)
    elif kind == "book":
        box(size * 0.18, size * margin, size * 0.82, size * (1 - margin))
        d.line([(size * 0.50, size * margin), (size * 0.50, size * (1 - margin))], fill=paper, width=thick)
    elif kind == "gear":
        cx = cy = size // 2
        teeth_n = rng.choice([6, 8, 10])
        r = size * rng.uniform(0.32, 0.40)
        for i in range(teeth_n):
            a = i * (2 * math.pi / teeth_n)
            d.line(
                [
                    (cx + r * 0.55 * math.cos(a), cy + r * 0.55 * math.sin(a)),
                    (cx + r * math.cos(a), cy + r * math.sin(a)),
                ],
                fill=ink,
                width=max(5, size // 14),
            )
        oval(cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55)
        d.ellipse([cx - r * 0.22, cy - r * 0.22, cx + r * 0.22, cy + r * 0.22], fill=paper)
    elif kind == "person":
        hr = size * rng.uniform(0.14, 0.20)
        oval(size * 0.50 - hr, size * 0.10, size * 0.50 + hr, size * 0.10 + 2 * hr)
        d.ellipse([size * 0.18, size * 0.45, size * 0.82, size * 0.95], fill=ink)
    elif kind == "star":
        cx = cy = size // 2
        pts = []
        points = rng.choice([5, 6, 8])
        for i in range(points * 2):
            a = -math.pi / 2 + i * math.pi / points
            rr = size * (0.42 if i % 2 == 0 else 0.18) * stretch
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        d.polygon(pts, fill=ink)
    elif kind == "heart":
        oval(size * 0.18, size * 0.20, size * 0.55, size * 0.55)
        oval(size * 0.45, size * 0.20, size * 0.82, size * 0.55)
        d.polygon([(size * 0.18, size * 0.40), (size * 0.50, size * 0.88), (size * 0.82, size * 0.40)], fill=ink)
    elif kind == "home":
        d.polygon(
            [(size * 0.50, size * margin), (size * margin, size * 0.48), (size * (1 - margin), size * 0.48)],
            fill=ink,
        )
        box(size * 0.24, size * 0.48, size * 0.76, size * (1 - margin))
        d.rectangle([size * 0.40, size * 0.58, size * 0.60, size * (1 - margin)], fill=paper)
    elif kind == "camera":
        box(size * margin, size * 0.30, size * (1 - margin), size * 0.82)
        box(size * 0.28, size * 0.18, size * 0.55, size * 0.30)
        oval(size * 0.32, size * 0.42, size * 0.68, size * 0.72)
        d.ellipse([size * 0.40, size * 0.50, size * 0.60, size * 0.64], fill=paper)
    elif kind == "bell":
        oval(size * 0.28, size * 0.15, size * 0.72, size * 0.55)
        d.polygon(
            [
                (size * 0.20, size * 0.48),
                (size * 0.80, size * 0.48),
                (size * 0.85, size * 0.72),
                (size * 0.15, size * 0.72),
            ],
            fill=ink,
        )
        box(size * 0.18, size * 0.70, size * 0.82, size * 0.78)
        oval(size * 0.42, size * 0.78, size * 0.58, size * 0.90)
    elif kind == "music":
        oval(size * 0.15, size * 0.55, size * 0.42, size * 0.85)
        oval(size * 0.48, size * 0.45, size * 0.75, size * 0.75)
        box(size * 0.34, size * 0.15, size * 0.44, size * 0.70)
        box(size * 0.66, size * 0.10, size * 0.76, size * 0.60)
        box(size * 0.34, size * 0.10, size * 0.76, size * 0.20)
    elif kind == "wifi":
        cx, cy = size // 2, size * 0.72
        for rad in (0.20, 0.34, 0.48):
            rad *= stretch
            d.arc(
                [cx - size * rad, cy - size * rad, cx + size * rad, cy + size * rad],
                start=200,
                end=340,
                fill=ink,
                width=thick,
            )
        oval(cx - m, cy - m, cx + m, cy + m)
    else:
        if style == 0:
            box(size * margin, size * margin, size * (1 - margin), size * (1 - margin))
            d.rectangle(
                [
                    size * (margin + 0.1),
                    size * (margin + 0.1),
                    size * (1 - margin - 0.1),
                    size * (1 - margin - 0.1),
                ],
                fill=paper,
            )
            oval(size * 0.35, size * 0.35, size * 0.65, size * 0.65)
        elif style == 1:
            d.polygon(
                [
                    (size * 0.50, size * margin),
                    (size * (1 - margin), size * 0.50),
                    (size * 0.50, size * (1 - margin)),
                    (size * margin, size * 0.50),
                ],
                fill=ink,
            )
        else:
            for _ in range(rng.randint(3, 6)):
                x = rng.randint(int(size * 0.15), int(size * 0.75))
                y = rng.randint(int(size * 0.15), int(size * 0.75))
                r = rng.randint(size // 16, size // 6)
                oval(x, y, x + r, y + r)

    angle = rng.choice([0, 0, 0, -8, 8, -12, 12, -20, 20])
    if angle:
        img = img.rotate(
            angle,
            expand=False,
            fillcolor=(255, 255, 255, 255),
            resample=Image.Resampling.BICUBIC,
        )
    return img


def sanitize(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    return name.strip("_").lower() or "icon"
