from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ProbeKind = Literal["tcp", "http"]
_KINDS: tuple[str, ...] = ("tcp", "http")

_TOP_KEYS = {"host", "interval", "timeout", "history", "warn_ms"}
_HOST_KEYS = {
    "name",
    "address",
    "kind",
    "port",
    "url",
    "method",
    "expect_status",
    "interval",
    "timeout",
}

_DEFAULT_INTERVAL = 5.0
_DEFAULT_TIMEOUT = 3.0
_DEFAULT_HISTORY = 240
_DEFAULT_WARN_MS = 300.0


class ConfigError(Exception):
    """Raised when the hosts file cannot be parsed or validated."""


@dataclass(slots=True, frozen=True)
class Host:
    name: str
    address: str
    kind: ProbeKind = "tcp"
    port: int | None = None
    url: str | None = None
    method: str = "HEAD"
    expect_status: int | None = None
    interval: float | None = None
    timeout: float | None = None

    @property
    def probe_url(self) -> str:
        """Full URL for http probes, derived from address when not set."""
        if self.kind != "http":
            return ""
        return self.url or f"https://{self.address}"

    @property
    def target(self) -> str:
        if self.kind == "http":
            return self.probe_url
        return f"{self.address}:{self.port}"


@dataclass(slots=True, frozen=True)
class Settings:
    hosts: tuple[Host, ...]
    interval: float = 5.0
    timeout: float = 3.0
    history: int = 240
    warn_ms: float = 300.0


def load_settings(path: Path) -> Settings:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {path}") from exc
    except IsADirectoryError as exc:
        raise ConfigError(f"config is a directory: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not valid UTF-8: {exc}") from exc
    return parse_settings(raw)


def parse_settings(raw: dict[str, Any]) -> Settings:
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown))}")

    entries = raw.get("host", [])
    if not isinstance(entries, list):
        raise ConfigError("'host' must be an array of tables ([[host]])")
    if not entries:
        raise ConfigError("no [[host]] entries defined")

    settings = Settings(
        interval=_positive(raw.get("interval", _DEFAULT_INTERVAL), "interval"),
        timeout=_positive(raw.get("timeout", _DEFAULT_TIMEOUT), "timeout"),
        history=_bounded_int(raw.get("history", _DEFAULT_HISTORY), "history", low=10),
        warn_ms=_positive(raw.get("warn_ms", _DEFAULT_WARN_MS), "warn_ms"),
        hosts=tuple(_parse_host(item, i) for i, item in enumerate(entries)),
    )

    names = [h.name for h in settings.hosts]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"duplicate host names: {', '.join(duplicates)}")
    return settings


def _parse_host(item: Any, index: int) -> Host:
    label = f"host #{index + 1}"
    if not isinstance(item, dict):
        raise ConfigError(f"{label}: expected a table, got {type(item).__name__}")
    unknown = set(item) - _HOST_KEYS
    if unknown:
        raise ConfigError(f"{label}: unknown keys: {', '.join(sorted(unknown))}")

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{label}: 'name' must be a non-empty string")
    name = name.strip()

    address = item.get("address")
    if not isinstance(address, str) or not address.strip():
        raise ConfigError(f"'{name}': 'address' must be a non-empty string")
    address = address.strip()

    kind = item.get("kind", "tcp")
    if kind not in _KINDS:
        raise ConfigError(f"'{name}': kind must be one of {_KINDS}, got {kind!r}")

    port = _optional_port(item.get("port"), name)
    if kind == "tcp":
        address, embedded_port = _split_address_port(address)
        if port is not None and embedded_port is not None and port != embedded_port:
            raise ConfigError(f"'{name}': 'port' conflicts with port inside 'address'")
        port = port if port is not None else embedded_port
        if port is None:
            raise ConfigError(
                f"'{name}': tcp probe needs a port (set 'port' or use 'address:port')"
            )
    else:
        port = None

    url: str | None = None
    method = "HEAD"
    expect_status: int | None = None
    if kind == "http":
        raw_url = item.get("url")
        if raw_url is not None and (not isinstance(raw_url, str) or not raw_url.strip()):
            raise ConfigError(f"'{name}': 'url' must be a non-empty string when set")
        url = (raw_url or f"https://{address}").strip()
        method = str(item.get("method", "HEAD")).upper()
        expect_status = _expect_status(item.get("expect_status"), name)

    return Host(
        name=name,
        address=address,
        kind=kind,
        port=port,
        url=url,
        method=method,
        expect_status=expect_status,
        interval=_positive_or_none(item.get("interval"), name),
        timeout=_positive_or_none(item.get("timeout"), name),
    )


def _split_address_port(address: str) -> tuple[str, int | None]:
    # "[2001:db8::1]:443" style
    if address.endswith("]") and "]:" in address:
        host, _, port_part = address.rpartition("]:")
        try:
            return host.lstrip("["), _valid_port(int(port_part))
        except ValueError:
            raise ConfigError(f"invalid port in address {address!r}") from None
    # bare IPv6 has no embedded port
    if address.count(":") > 1 or address.endswith(":"):
        return address, None
    host, sep, port_part = address.rpartition(":")
    if not sep or not port_part.isdigit():
        return address, None
    try:
        return host, _valid_port(int(port_part))
    except ValueError:
        raise ConfigError(f"invalid port in address {address!r}") from None


def _optional_port(value: Any, name: str) -> int | None:
    if value is None:
        return None
    try:
        return _valid_port(int(value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'{name}': invalid port {value!r}") from exc


def _valid_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port


def _expect_status(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ConfigError(f"'{name}': 'expect_status' must be an integer in [100, 599]")
    return value


def _positive(value: Any, key: str) -> float:
    number = _as_number(value, key)
    if number <= 0:
        raise ConfigError(f"'{key}' must be > 0, got {value!r}")
    return number


def _positive_or_none(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return _positive(value, name)


def _as_number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{key}' must be a number, got {value!r}")
    return float(value)


def _bounded_int(value: Any, key: str, low: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{key}' must be an integer, got {value!r}")
    if value < low:
        raise ConfigError(f"'{key}' must be >= {low}, got {value}")
    return value
