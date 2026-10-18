import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_benchmark_runs_and_its_internal_checks_pass():
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "benchmarks" / "index_benchmark.py"),
            "--sizes", "300",
            "--lookups", "10",
            "--scan-lookups", "2",
            "--repeats", "1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Experiment A" in result.stdout
    assert "Experiment B" in result.stdout