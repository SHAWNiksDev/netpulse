from __future__ import annotations

import pytest

from netpulse.config import ConfigError, Host, load_settings, parse_settings


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


@pytest.mark.parametrize(
    ("address", "expected_host", "expected_port"),
    [("[2001:db8::1]:443", "2001:db8::1", 443), ("[::1]:22", "::1", 22)],
)
def test_bracketed_ipv6_with_embedded_port(address, expected_host, expected_port) -> None:
    settings = parse_settings({"host": [{"name": "v6", "address": address}]})
    (host,) = settings.hosts
    assert host.address == expected_host
    assert host.port == expected_port
    assert host.target == address  # round-trips to the familiar notation


def test_bracketed_ipv6_without_port_strips_brackets() -> None:
    settings = parse_settings({"host": [{"name": "v6", "address": "[::1]", "port": 22}]})
    (host,) = settings.hosts
    assert host.address == "::1"
    assert host.port == 22


def test_bracketed_ipv6_embedded_port_conflict_rejected() -> None:
    with pytest.raises(ConfigError, match="conflicts"):
        parse_settings({"host": [{"name": "v6", "address": "[::1]:22", "port": 33}]})


@pytest.mark.parametrize("address", ["[::1]:", "[::1", "[2001:db8::1]:abc"])
def test_malformed_bracketed_address_rejected(address: str) -> None:
    with pytest.raises(ConfigError, match="address"):
        parse_settings({"host": [{"name": "v6", "address": address}]})


def test_non_numeric_port_in_address_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid port"):
        parse_settings(minimal_raw(address="example.com:ssh"))


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


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_numbers_rejected(value: float) -> None:
    with pytest.raises(ConfigError, match="finite"):
        parse_settings({**minimal_raw(), "interval": value})
    with pytest.raises(ConfigError, match="finite"):
        parse_settings({**minimal_raw(), "warn_ms": value})


def test_boolean_port_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid port"):
        parse_settings(minimal_raw(port=True))


def test_float_port_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid port"):
        parse_settings(minimal_raw(port=443.9))


def test_unknown_http_method_rejected() -> None:
    with pytest.raises(ConfigError, match="'method'"):
        parse_settings(minimal_raw(kind="http", port=None, method="BREW"))


def test_port_on_http_host_rejected() -> None:
    with pytest.raises(ConfigError, match=r"port.*only applies"):
        parse_settings(minimal_raw(kind="http", port=443))


def test_http_keys_on_tcp_host_rejected() -> None:
    with pytest.raises(ConfigError, match="only apply"):
        parse_settings(minimal_raw(url="https://example.com"))


def test_unreadable_config_becomes_config_error(tmp_path) -> None:
    config = tmp_path / "hosts.toml"
    config.write_text('[[host]]\nname = "x"\naddress = "127.0.0.1"\nport = 1\n')
    config.chmod(0o000)
    try:
        with pytest.raises(ConfigError, match="cannot read"):
            load_settings(config)
    finally:
        config.chmod(0o644)


def test_expect_status_out_of_range_rejected() -> None:
    with pytest.raises(ConfigError, match=r"expect_status.*\[100, 599\]"):
        parse_settings(minimal_raw(kind="http", expect_status=999, port=None))


def test_empty_name_rejected() -> None:
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_settings(minimal_raw(name="   "))


def test_no_hosts_rejected() -> None:
    with pytest.raises(ConfigError, match=r"no \[\[host\]\] entries"):
        parse_settings({})


@pytest.mark.parametrize("address", ["[]", "[]:22", "example.com:", "[no-ip]:22", "::oops"])
def test_invalid_tcp_addresses_rejected(address: str) -> None:
    with pytest.raises(ConfigError, match="address"):
        parse_settings(minimal_raw(address=address, port=22))


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "https://", "https://[::1", "https://example.com:70000"]
)
def test_invalid_http_urls_rejected(url: str) -> None:
    with pytest.raises(ConfigError, match="url"):
        parse_settings(minimal_raw(kind="http", port=None, url=url))


def test_http_ipv6_address_gets_bracketed_url() -> None:
    settings = parse_settings(minimal_raw(kind="http", port=None, address="::1"))
    assert settings.hosts[0].probe_url == "https://[::1]"
    assert Host(name="v6", address="::1", kind="http").probe_url == "https://[::1]"


def test_oversized_number_reports_config_error() -> None:
    with pytest.raises(ConfigError, match="finite"):
        parse_settings({**minimal_raw(), "interval": 10**1000})
