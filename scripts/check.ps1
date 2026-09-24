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
# pytest-xdist で並列実行する。ワーカー数はコア数に合わせ、起動コストが勝つ 8 で頭打ちにする。
Invoke-Check "pytest" @($Python, "-m", "pytest", "-q", "-n", "auto", "--maxprocesses=8")
Invoke-Check "git diff whitespace check" @("git", "diff", "--check")

Write-Host "All checks passed."
