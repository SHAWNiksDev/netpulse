from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_settings
from .ui.app import NetPulseApp


def _default_configs() -> tuple[Path, ...]:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return (Path("hosts.toml"), config_home / "netpulse" / "hosts.toml")


DEFAULT_CONFIGS = _default_configs()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netpulse",
        description="Async TUI dashboard that watches hosts over TCP and HTTP probes.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="path to hosts.toml (default: ./hosts.toml, then ~/.config/netpulse/hosts.toml)",
    )
    parser.add_argument("--version", action="version", version=f"netpulse {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config_path = args.config
    if config_path is None:
        config_path = next((p for p in DEFAULT_CONFIGS if p.is_file()), DEFAULT_CONFIGS[0])

    try:
        settings = load_settings(config_path)
    except ConfigError as exc:
        print(f"netpulse: {exc}", file=sys.stderr)
        return 2

    NetPulseApp(settings, config_path=config_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
