from pathlib import Path
import pytest
from minidb.config import Config, ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent

def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path

def test_defaults_when_no_path():
    cfg = load_config(None)
    assert cfg == Config()
    assert cfg.database.page_size == 4096
    assert cfg.logging.level == "INFO"

def test_values_loaded_from_file(tmp_path):
    p = write(
        tmp_path / "c.toml",
        '[database]\ndata_dir = "mydata"\npage_size = 8192\n[logging]\nlevel = "debug"\n',
    )
    cfg = load_config(p)
    assert cfg.database.data_dir == Path("mydata")
    assert cfg.database.page_size == 8192
    assert cfg.logging.level == "DEBUG"  # normalised to upper case

def test_missing_sections_keep_defaults(tmp_path):
    p = write(tmp_path / "c.toml", '[logging]\nlevel = "WARNING"\n')
    cfg = load_config(p)
    assert cfg.database == Config().database
    assert cfg.logging.level == "WARNING"

def test_unknown_key_rejected(tmp_path):
    p = write(tmp_path / "c.toml", "[database]\npage_sizee = 4096\n")
    with pytest.raises(ConfigError, match="page_sizee"):
        load_config(p)

def test_unknown_section_rejected(tmp_path):
    p = write(tmp_path / "c.toml", "[network]\nport = 1\n")
    with pytest.raises(ConfigError, match="network"):
        load_config(p)

@pytest.mark.parametrize("bad", [0, -4096, 1000, 131072])
def test_invalid_page_size_rejected(tmp_path, bad):
    p = write(tmp_path / "c.toml", f"[database]\npage_size = {bad}\n")
    with pytest.raises(ConfigError):
        load_config(p)

def test_invalid_log_level_rejected(tmp_path):
    p = write(tmp_path / "c.toml", '[logging]\nlevel = "LOUD"\n')
    with pytest.raises(ConfigError):
        load_config(p)

def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")

def test_shipped_config_is_valid():
    cfg = load_config(REPO_ROOT / "config" / "minidb.toml")
    assert cfg.database.page_size == 4096