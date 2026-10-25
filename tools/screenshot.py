"""Render the dashboard headlessly and save an SVG screenshot to docs/.

Usage: .venv/bin/python tools/screenshot.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from netpulse.config import Host, Settings
from netpulse.ui.app import NetPulseApp


async def serve_forever(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.read(256)
    writer.close()


async def main() -> None:
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
            for _ in range(14):
                await asyncio.sleep(0.6)
                await pilot.pause()
            svg = app.export_screenshot()
            out = Path(__file__).resolve().parents[1] / "docs" / "screenshot.svg"
            out.write_text(svg)
            print(f"saved {out}")
    finally:
        loopback.close()
        await loopback.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
