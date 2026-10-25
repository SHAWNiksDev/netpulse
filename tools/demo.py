"""Render an animated GIF demo of the dashboard into docs/demo.gif.

Usage: .venv/bin/python tools/demo.py   (needs the 'tools' extra: cairosvg, Pillow)
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cairosvg
from PIL import Image

from netpulse.config import Host, Settings
from netpulse.ui.app import NetPulseApp

FRAME_NUM = [0]


@contextmanager
def frame_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="netpulse_frames") as tmp:
        yield Path(tmp)


async def serve_forever(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.read(256)
    writer.close()


def svg_to_png(svg: str, out: Path) -> None:
    svg = svg.replace("Fira Code, monospace", "DejaVu Sans Mono, monospace")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=1200)


async def capture(
    app: NetPulseApp, pilot, frame_path: Path, seconds: float, step: float = 0.8
) -> None:
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        await asyncio.sleep(step)
        await pilot.pause()
        svg_to_png(app.export_screenshot(), frame_path / f"frame_{FRAME_NUM[0]:03d}.png")
        FRAME_NUM[0] += 1


async def record(frame_path: Path) -> None:
    loopback = await asyncio.start_server(serve_forever, "127.0.0.1", 0)
    try:
        port = loopback.sockets[0].getsockname()[1]

        settings = Settings(
            hosts=(
                Host(name="localhost", address="127.0.0.1", port=port),
                Host(name="cloudflare-dns", address="1.1.1.1", port=443),
                Host(name="archlinux", address="archlinux.org", kind="http", method="HEAD"),
                Host(name="github", address="github.com", kind="http", method="HEAD"),
                Host(name="closed-port", address="127.0.0.1", port=9),
            ),
            interval=0.8,
            timeout=2.5,
            history=60,
            warn_ms=250,
        )
        app = NetPulseApp(settings, config_path=Path("hosts.toml"))
        async with app.run_test(size=(150, 36)) as pilot:
            await pilot.pause()
            await capture(app, pilot, frame_path, 7.0)
            await pilot.press("s")
            await capture(app, pilot, frame_path, 1.6)
            await pilot.press("?")
            await capture(app, pilot, frame_path, 2.4)
            await pilot.press("escape")
            await pilot.press("space")
            await capture(app, pilot, frame_path, 1.6)
    finally:
        loopback.close()
        await loopback.wait_closed()


def main() -> None:
    with frame_dir() as frame_path:
        asyncio.run(record(frame_path))
        frames = sorted(frame_path.glob("frame_*.png"))
        if not frames:
            raise SystemExit("no frames captured; nothing to assemble")
        images = [
            Image.open(f).convert("P", palette=Image.Palette.ADAPTIVE, colors=64) for f in frames
        ]
        out = Path(__file__).resolve().parents[1] / "docs" / "demo.gif"
        images[0].save(out, save_all=True, append_images=images[1:], duration=800, loop=0)
        print(f"saved {out} ({out.stat().st_size // 1024} KiB, {len(images)} frames)")


if __name__ == "__main__":
    main()
