import pytest


@pytest.fixture
def config_file(tmp_path):
    """A config whose data directory lives inside pytest's temp folder."""
    data_dir = tmp_path / "data"
    cfg = tmp_path / "minidb.toml"
    # TOML single-quoted strings are literal, so Windows backslashes are safe.
    cfg.write_text(f"[database]\ndata_dir = '{data_dir}'\n", encoding="utf-8")
    return cfg