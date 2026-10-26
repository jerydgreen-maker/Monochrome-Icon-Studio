#!/usr/bin/env python3
"""Create the Windows application icon from logo.jpg."""
from pathlib import Path

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    root = Path(__file__).resolve().parent
    logo = Image.open(root / "logo.jpg").convert("RGBA")
    frames = [logo.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES]
    frames[0].save(
        root / "app_icon.ico",
        format="ICO",
        sizes=[frame.size for frame in frames],
        append_images=frames[1:],
    )


if __name__ == "__main__":
    main()
