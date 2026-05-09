$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot
try {
    if (-not (Test-Path "resources\icons\app_icon.ico")) {
        throw "resources\icons\app_icon.ico is missing."
    }

    python -m ruff check . --no-cache
    python -m pyright
    python -m pytest

    Remove-Item -LiteralPath "dist\OmniDesk" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "dist\OmniDesk.exe" -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "dist\OmniDesk.zip" -Force -ErrorAction SilentlyContinue

    pyinstaller `
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
