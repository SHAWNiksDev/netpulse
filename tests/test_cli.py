from __future__ import annotations

from pathlib import Path

import pytest

from netpulse import __version__, cli


def test_missing_config_reports_error_and_exits_2(tmp_path: Path, capsys) -> None:
    assert cli.main(["-c", str(tmp_path / "nope.toml")]) == 2
    assert "config not found" in capsys.readouterr().err


def test_invalid_config_reports_error_and_exits_2(tmp_path: Path, capsys) -> None:
    config = tmp_path / "hosts.toml"
    config.write_text("bogus = 1\n", encoding="utf-8")
    assert cli.main(["-c", str(config)]) == 2
    assert "unknown top-level keys: bogus" in capsys.readouterr().err


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert f"netpulse {__version__}" in capsys.readouterr().out


def test_default_configs_follow_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli._default_configs() == (
        Path("hosts.toml"),
        tmp_path / "netpulse" / "hosts.toml",
    )


def test_default_configs_ignore_empty_xdg(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    configs = cli._default_configs()
    assert configs[1] == Path.home() / ".config" / "netpulse" / "hosts.toml"


def test_fallback_picks_first_existing_config(tmp_path: Path, monkeypatch, capsys) -> None:
    fallback = tmp_path / "fallback.toml"
    fallback.write_text("bogus = 1\n", encoding="utf-8")
    monkeypatch.setattr(cli, "DEFAULT_CONFIGS", (tmp_path / "missing.toml", fallback))
    # the reported error comes from the fallback file, proving it was chosen
    assert cli.main([]) == 2
    assert "bogus" in capsys.readouterr().err


def test_check_validates_config_without_starting_app(tmp_path: Path, monkeypatch, capsys) -> None:
    config = tmp_path / "hosts.toml"
    config.write_text('[[host]]\nname = "local"\naddress = "127.0.0.1"\nport = 80\n')

    def unexpected_app(*args, **kwargs):
        pytest.fail("--check must not start the dashboard or probe any hosts")

    monkeypatch.setattr(cli, "NetPulseApp", unexpected_app)
    assert cli.main(["--check", "-c", str(config)]) == 0
    assert "configuration OK (1 host)" in capsys.readouterr().out


def test_check_returns_config_error_for_invalid_url(tmp_path: Path, capsys) -> None:
    config = tmp_path / "hosts.toml"
    config.write_text(
        '[[host]]\nname = "bad"\naddress = "example.com"\nkind = "http"\n'
        'url = "ftp://example.com"\n'
    )
    assert cli.main(["--check", "-c", str(config)]) == 2
    captured = capsys.readouterr()
    assert "invalid 'url'" in captured.err
    assert not captured.out
