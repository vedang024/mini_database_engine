# Create the virtual environment and install dependencies.
# Run from the repo root:  .\scripts\setup.ps1
$ErrorActionPreference = "Stop"
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host "Done. Activate with: .\.venv\Scripts\Activate.ps1"