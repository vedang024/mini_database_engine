import subprocess
import sys


def run_minidb(config_file, *args):
    """Run MiniDB in a brand-new OS process, exactly like a real restart."""
    return subprocess.run(
        [sys.executable, "-m", "minidb", "--config", str(config_file), *args],
        capture_output=True,
        text=True,
    )


def test_record_survives_process_restart(config_file):
    first = run_minidb(config_file, "insert", "--id", "10", "--name", "Ann", "--email", "ann@x.com")
    assert first.returncode == 0, first.stderr

    second = run_minidb(config_file, "scan")  # a different process
    assert second.returncode == 0, second.stderr
    assert "10 | Ann | ann@x.com" in second.stdout