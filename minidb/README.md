# MiniDB

A database engine built from scratch in Python, phase by phase, to learn how
databases work internally: storage, indexing, query execution, transactions,
recovery, and networking.

## Status

Phase 1: project skeleton (config, logging, CLI entry point, tests).

## Requirements

- Python 3.11+

## Quick start (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m minidb --config config\minidb.toml
pytest -v
```

## Layout

- `src/minidb/` the engine package
- `tests/` pytest suite
- `config/` configuration files
- `scripts/` developer helper scripts


