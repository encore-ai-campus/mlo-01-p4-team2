$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -u "$PSScriptRoot\run-split.py" --bind 0.0.0.0
