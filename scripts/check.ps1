$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
$Python = if (Test-Path ".\.venv\Scripts\python.exe") {
    ".\.venv\Scripts\python.exe"
} else {
    "python"
}

function Invoke-Check {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    Write-Host "==> $Label"
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Invoke-Check "ruff check" @($Python, "-m", "ruff", "check", ".", "--no-cache")
Invoke-Check "ruff format" @($Python, "-m", "ruff", "format", ".", "--check")
Invoke-Check "pyright" @($Python, "-m", "pyright")
Invoke-Check "pytest" @($Python, "-m", "pytest", "-q")
Invoke-Check "git diff whitespace check" @("git", "diff", "--check")

Write-Host "All checks passed."
