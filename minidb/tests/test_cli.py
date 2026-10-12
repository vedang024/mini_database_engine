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

def test_insert_then_scan(config_file, capsys):
    argv = ["--config", str(config_file)]
    assert main(argv + ["insert", "--id", "1", "--name", "Ann", "--email", "ann@x.com"]) == 0
    assert main(argv + ["scan"]) == 0
    out = capsys.readouterr().out
    assert "1 | Ann | ann@x.com" in out
    assert "(1 row(s))" in out


def test_scan_on_fresh_database(config_file, capsys):
    assert main(["--config", str(config_file), "scan"]) == 0
    assert "(0 row(s))" in capsys.readouterr().out

def test_non_database_file_returns_error_code(config_file, tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "minidb.db").write_bytes(b"this is not a database")
    assert main(["--config", str(config_file), "scan"]) == 1
    assert "not a MiniDB file" in capsys.readouterr().err

def test_stats_command(config_file, capsys):
    argv = ["--config", str(config_file)]
    assert main(argv + ["insert", "--id", "1", "--name", "Ann", "--email", "ann@x.com"]) == 0
    capsys.readouterr()  # discard the output of insert

    assert main(argv + ["stats"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines[0].split() == ["page", "records", "used", "free"]
    # page 1: 1 record; used = 8 header + 4 length prefix + 24 payload = 36; free = 4096 - 36
    assert lines[1].split() == ["1", "1", "36", "4060"]