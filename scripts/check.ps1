$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

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

Invoke-Check "pytest" @("pytest", "-q")
Invoke-Check "ruff check" @("python", "-m", "ruff", "check", ".", "--no-cache")
Invoke-Check "ruff format" @("python", "-m", "ruff", "format", ".", "--check")
Invoke-Check "pyright" @("python", "-m", "pyright")
Invoke-Check "git diff whitespace check" @("git", "diff", "--check")

Write-Host "All checks passed."
