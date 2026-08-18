#!/usr/bin/env python3
"""Generate deterministic, dependency-free PNG assets for both theme variants."""

import binascii
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# The dark variant lives at the repository root: `omarchy theme install` clones a
# repo straight into ~/.config/omarchy/themes/<name>, so it only ever sees one
# theme, built from the files at the top level.
#
# There is no wallpaper artwork on purpose: the desktop is a single flat fill so
# it disappears behind the windows the way the classic desktop did instead of
# reading as a picture. The dark fill matches the source editor background;
# the light fill remains one step below its palette surfaces.
THEMES = {
    "macos-classic-light": {
        "directory": ROOT / "macos-classic-light",
        "color": "#D8D8D8",
    },
    "macos-classic-dark": {
        "directory": ROOT,
        "color": "#131313",
    },
}


def rgb(value):
    value = value.removeprefix("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload))


def render_png(path, width, height, colors):
    row = b"\x00" + bytes(rgb(colors["color"])) * width

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    payload += chunk(b"IDAT", zlib.compress(row * height, level=9))
    payload += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main():
    for name, colors in THEMES.items():
        theme = colors["directory"]
        render_png(theme / "backgrounds" / f"{name}.png", 1920, 1080, colors)
        render_png(theme / "unlock.png", 1920, 1080, colors)
        if name == "macos-classic-dark":
            render_png(theme / "preview.png", 640, 360, colors)
        render_png(theme / "preview-unlock.png", 640, 360, colors)


if __name__ == "__main__":
    main()
