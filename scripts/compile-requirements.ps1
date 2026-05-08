$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$env:PIP_TOOLS_CACHE_DIR = Join-Path $repoRoot "tmp\pip-tools-cache"

python -m piptools compile `
    requirements-dev.in `
    --output-file requirements.txt `
    --strip-extras `
    --no-emit-index-url `
    --no-emit-trusted-host
if ($LASTEXITCODE -ne 0) {
    throw "pip-compile failed with exit code $LASTEXITCODE"
}

Write-Host "requirements.txt updated."
