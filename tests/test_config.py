from __future__ import annotations

import pytest

from netpulse.config import ConfigError, Host, parse_settings


def minimal_raw(**host_overrides) -> dict:
    host = {"name": "h", "address": "example.com", "port": 443}
    host.update(host_overrides)
    return {"host": [host]}


def test_minimal_tcp_host_gets_defaults() -> None:
    settings = parse_settings(minimal_raw())
    (host,) = settings.hosts
    assert host.kind == "tcp"
    assert host.port == 443
    assert host.target == "example.com:443"
    assert settings.interval == 5.0
    assert settings.timeout == 3.0
    assert settings.history == 240
    assert settings.warn_ms == 300.0


def test_global_defaults_are_overridable() -> None:
    raw = minimal_raw()
    raw.update(interval=0.5, timeout=9, history=25, warn_ms=1234)
    settings = parse_settings(raw)
    assert settings.interval == 0.5
    assert settings.timeout == 9.0
    assert settings.history == 25
    assert settings.warn_ms == 1234.0


def test_port_inside_address() -> None:
    settings = parse_settings(minimal_raw(address="example.com:8080", port=None))
    (host,) = settings.hosts
    assert host.address == "example.com"
    assert host.port == 8080


@pytest.mark.parametrize("address", ["::1", "2001:db8::1"])
def test_ipv6_without_port_is_untouched(address: str) -> None:
    settings = parse_settings({"host": [{"name": "v6", "address": address, "port": 22}]})
    (host,) = settings.hosts
    assert host.address == address
    assert host.port == 22


def test_conflicting_ports_rejected() -> None:
    with pytest.raises(ConfigError, match="conflicts"):
        parse_settings(minimal_raw(address="example.com:8080", port=443))


def test_tcp_without_any_port_rejected() -> None:
    with pytest.raises(ConfigError, match="needs a port"):
        parse_settings(minimal_raw(port=None))


def test_http_defaults_to_https_url() -> None:
    settings = parse_settings({"host": [{"name": "web", "address": "example.com", "kind": "http"}]})
    (host,) = settings.hosts
    assert host.url == "https://example.com"
    assert host.method == "HEAD"
    assert host.expect_status is None


def test_http_explicit_url_and_expect_status() -> None:
    settings = parse_settings(
        {
            "host": [
                {
                    "name": "health",
                    "address": "example.com",
                    "kind": "http",
                    "url": "http://example.com/healthz",
                    "method": "get",
                    "expect_status": 204,
                }
            ]
        }
    )
    (host,) = settings.hosts
    assert host.url == "http://example.com/healthz"
    assert host.method == "GET"
    assert host.expect_status == 204


def test_directly_built_http_host_derives_url() -> None:
    # regression: Host objects built in code (not via TOML) must still get a URL
    host = Host(name="gh", address="github.com", kind="http")
    assert host.probe_url == "https://github.com"
    assert host.target == "https://github.com"


def test_duplicate_names_rejected() -> None:
    raw = {"host": [dict(name="dup", address="a", port=1), dict(name="dup", address="b", port=2)]}
    with pytest.raises(ConfigError, match="duplicate"):
        parse_settings(raw)


def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown top-level"):
        parse_settings({**minimal_raw(), "intervall": 5})


def test_unknown_host_key_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown keys"):
        parse_settings(minimal_raw(proto="udp"))


def test_bad_kind_rejected() -> None:
    with pytest.raises(ConfigError, match="kind must be one of"):
        parse_settings(minimal_raw(kind="icmp"))


def test_non_positive_interval_rejected() -> None:
    with pytest.raises(ConfigError, match="'interval'"):
        parse_settings({**minimal_raw(), "interval": 0})


def test_expect_status_out_of_range_rejected() -> None:
    with pytest.raises(ConfigError, match=r"expect_status.*\[100, 599\]"):
        parse_settings(minimal_raw(kind="http", expect_status=999))


def test_empty_name_rejected() -> None:
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_settings(minimal_raw(name="   "))


def test_no_hosts_rejected() -> None:
    with pytest.raises(ConfigError, match=r"no \[\[host\]\] entries"):
        parse_settings({})
