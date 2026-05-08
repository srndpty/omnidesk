$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

python -m pytest -q -n 2
if ($LASTEXITCODE -ne 0) {
    throw "parallel pytest failed with exit code $LASTEXITCODE"
}

Write-Host "Parallel pytest passed."
