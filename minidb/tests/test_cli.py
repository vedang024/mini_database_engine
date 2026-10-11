import pytest
from minidb import __version__
from minidb.cli import main

def test_main_runs_with_defaults(capsys):
    assert main([]) == 0
    assert "starting" in capsys.readouterr().err  # logs go to stderr

def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out

def test_missing_config_returns_error_code(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml")]) == 2
    assert "error" in capsys.readouterr().err

def test_invalid_config_returns_error_code(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[database]\npage_size = 1000\n", encoding="utf-8")
    assert main(["--config", str(bad)]) == 2

def test_log_level_from_config_is_applied(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[logging]\nlevel = "ERROR"\n', encoding="utf-8")
    assert main(["--config", str(cfg)]) == 0
    assert "starting" not in capsys.readouterr().err  # INFO suppressed