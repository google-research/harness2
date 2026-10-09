#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Render installation, JobBench setup, and a run in one terminal line (requires Pillow).

Run: python scripts/render_readme_typing.py
Optional: --preview /tmp/harness2-setup.png --font /path/to/monospace.ttf

The 2560x256 GIF is displayed at 640x64 CSS pixels. Preserve its native pixels
when saving: downsampling before embedding defeats high-DPI text rendering.
Commands are illustrated, not executed. Prerequisites remain in Install and
docs/BENCHMARKS.md; clicking the animation opens the JobBench guide.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, SCALE = 640, 64, 4
BACKGROUND = "#232d43"
TEXT = "#f2effa"
PURPLE = "#c6acf5"
BORDER = "#46516b"
COMMANDS = (
    "uv pip install -e '.[render]'",
    "harness2-setup jb",
    "harness2 --bench jb --k 1 --mode parallel",
)


def find_font(requested):
    candidates = [requested] if requested else [
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("Provide a monospace TrueType font with --font.")


def render_frame(font_path, command, length, cursor=True):
    canvas = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), "white")
    draw = ImageDraw.Draw(canvas)
    mono = ImageFont.truetype(font_path, 20 * SCALE)
    full_width = draw.textlength(command, font=mono)
    text_x = 102 * SCALE
    if text_x + full_width + 24 * SCALE > WIDTH * SCALE:
        raise SystemExit("Setup command is too long for the image width.")
    draw.rounded_rectangle(
        (SCALE, SCALE, (WIDTH - 1) * SCALE, (HEIGHT - 1) * SCALE),
        radius=10 * SCALE, fill=BACKGROUND, outline=BORDER, width=SCALE,
    )
    # Minimal window controls stay on the same row as the text.
    for x, color in ((21, "#63708b"), (33, "#8b7daf"), (45, "#b39ace")):
        draw.ellipse(
            ((x - 3) * SCALE, 29 * SCALE, (x + 3) * SCALE, 35 * SCALE), fill=color,
        )
    draw.line((61 * SCALE, 20 * SCALE, 61 * SCALE, 44 * SCALE), fill=BORDER, width=SCALE)
    baseline = 39 * SCALE
    draw.text((77 * SCALE, baseline), "$", font=mono, fill=PURPLE, anchor="ls")
    # Type a real executable and its arguments; no simulated success output.
    visible = command[:length]
    name_length = len(command.split(" ", 1)[0])
    executable, arguments = visible[:name_length], visible[name_length:]
    draw.text((text_x, baseline), executable, font=mono, fill=PURPLE, anchor="ls")
    args_x = text_x + draw.textlength(executable, font=mono)
    draw.text((args_x, baseline), arguments, font=mono, fill=TEXT, anchor="ls")
    if cursor:
        right = text_x + draw.textlength(visible, font=mono) + 3 * SCALE
        draw.rectangle(
            (right, 21 * SCALE, right + 2 * SCALE, 43 * SCALE), fill=PURPLE,
        )
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/harness2-setup.gif")
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    font_path = find_font(args.font)
    documented = {
        line.strip()
        for path in (ROOT / "README.md", ROOT / "docs/BENCHMARKS.md")
        for line in path.read_text().splitlines()
    }
    for command in COMMANDS:
        if command not in documented:
            raise SystemExit(f"Command no longer matches the setup documentation: {command}")
    stills = [render_frame(font_path, command, len(command)) for command in COMMANDS]
    palette_source = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE * len(stills)))
    for index, still in enumerate(stills):
        palette_source.paste(still, (0, index * HEIGHT * SCALE))
    palette = palette_source.quantize(colors=256)
    frames, durations = [], []

    def add(command, length, duration, cursor=True):
        frame = render_frame(font_path, command, length, cursor)
        frames.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))
        durations.append(duration)

    for command in COMMANDS:
        add(command, 0, 400)
        for length in range(1, len(command) + 1):
            add(command, length, 60 if command[length - 1] != " " else 100)
        for _ in range(3):
            add(command, len(command), 450, cursor=False)
            add(command, len(command), 450)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        args.output, save_all=True, append_images=frames[1:], duration=durations,
        loop=0, disposal=1, optimize=False,
    )
    if args.preview:
        stills[-1].save(args.preview)
    print(f"Saved {args.output}: {WIDTH * SCALE}x{HEIGHT * SCALE} "
          f"(display {WIDTH}x{HEIGHT}), {sum(durations) / 1000:.2f}s, "
          f"{args.output.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
