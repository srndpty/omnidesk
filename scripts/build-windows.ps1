$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = if (Test-Path (Join-Path $RepoRoot ".venv\Scripts\python.exe")) {
    Join-Path $RepoRoot ".venv\Scripts\python.exe"
} else {
    "python"
}

function Invoke-PythonModule {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Module,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $Python -m $Module @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "python -m $Module failed with exit code $LASTEXITCODE"
    }
}

Push-Location $RepoRoot
try {
    if (-not (Test-Path "resources\icons\app_icon.ico")) {
        throw "resources\icons\app_icon.ico is missing."
    }

    Invoke-PythonModule ruff check . --no-cache
    Invoke-PythonModule pyright
    Invoke-PythonModule pytest

    Remove-Item -LiteralPath "dist\OmniDesk" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "dist\OmniDesk.exe" -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "dist\OmniDesk.zip" -Force -ErrorAction SilentlyContinue

    Invoke-PythonModule PyInstaller `
        --clean `
        --noconfirm `
        --workpath "tmp\pyinstaller-build" `
        --distpath "dist" `
        "packaging\pyinstaller\OmniDesk.spec"

    if (-not (Test-Path "dist\OmniDesk\OmniDesk.exe")) {
        throw "dist\OmniDesk\OmniDesk.exe was not created."
    }
    if (-not (Test-Path "dist\OmniDesk\_internal")) {
        throw "dist\OmniDesk\_internal was not created."
    }

    Compress-Archive -Path "dist\OmniDesk\*" -DestinationPath "dist\OmniDesk.zip" -Force
} finally {
    Pop-Location
}
