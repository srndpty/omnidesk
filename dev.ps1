<#
.SYNOPSIS
    OmniDesk 開発者向け統一コマンドインターフェース。

.DESCRIPTION
    リポジトリ固有の build / run / test / lint / check コマンドをまとめた薄い入口です。
    実処理は既存の `scripts\*.ps1` と既存のツールコマンドへそのまま委譲し、
    ここで同等処理を再実装することはしません。

.EXAMPLE
    .\dev.ps1 help
    .\dev.ps1 check
    .\dev.ps1 test -k thumbnail
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

Set-StrictMode -Version Latest

# repo root はスクリプト自身の位置から解決する（絶対パスはハードコードしない）。
$RepoRoot = $PSScriptRoot
$ScriptsDir = Join-Path $RepoRoot "scripts"

# scripts\check.ps1 / build-windows.ps1 と同じ Python 解決規則。
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }

$script:ExitCode = 0

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# 外部コマンドを実行し、終了コードを $script:ExitCode に記録する。
# 既に失敗している場合は後続ステップを実行しない。
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    if ($script:ExitCode -ne 0) { return }

    $global:LASTEXITCODE = 0
    if ($Arguments.Count -gt 0) {
        & $FilePath @Arguments
    } else {
        & $FilePath
    }
    $script:ExitCode = $LASTEXITCODE
}

# 既存の repo スクリプト（失敗時に throw する作り）を呼び出す。
function Invoke-RepoScript {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @()
    )

    if ($script:ExitCode -ne 0) { return }

    $path = Join-Path $ScriptsDir $Name
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "scripts\$Name が見つかりません。" -ForegroundColor Red
        $script:ExitCode = 1
        return
    }

    $global:LASTEXITCODE = 0
    try {
        if ($Arguments.Count -gt 0) {
            & $path @Arguments
        } else {
            & $path
        }
        $script:ExitCode = $LASTEXITCODE
    } catch {
        # check.ps1 などは失敗時に throw するため、ここで non-zero へ落とす。
        Write-Host $_.Exception.Message -ForegroundColor Red
        $script:ExitCode = if ($LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 }
    }
}

# 委譲先が追加引数を受け取らないコマンドで、黙って捨てないための門番。
# scripts\build-windows.ps1 と scripts\check.ps1 は param ブロックを持たず $args も
# 見ないため、渡しても無視される。効いたと誤解させないよう、ここで失敗させる。
function Assert-NoExtraArguments {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @()
    )

    if ($Arguments.Count -eq 0) { return $true }

    Write-Host "$Name は追加引数を受け取りません: $($Arguments -join ' ')" -ForegroundColor Red
    Write-Host "個別のオプションが必要な場合は、委譲先を直接実行してください。" -ForegroundColor Red
    $script:ExitCode = 2
    return $false
}

function Show-Help {
    Write-Host @"
dev - OmniDesk 開発コマンド

使い方:
  .\dev.ps1 <command> [追加引数...]

コマンド:
  build     Windows 配布ビルド        -> scripts\build-windows.ps1
            (ruff / pyright / pytest の後に PyInstaller と dist\OmniDesk.zip)
  run       アプリを起動              -> python -m omnidesk （gui の別名）
  gui       GUI アプリを起動          -> python -m omnidesk
  test      テストを実行              -> python -m pytest
  lint      lint / 静的解析           -> ruff check, ruff format --check, pyright
  check     コミット前の正式な品質ゲート -> scripts\check.ps1
  clean     生成物のみ削除            -> dist / build / PyInstaller 作業ディレクトリ等
  help      このヘルプを表示

補足:
  - test / gui の追加引数はそのまま委譲先へ渡ります。例: .\dev.ps1 test -k thumbnail
  - lint の追加引数は ruff check にだけ渡ります（3つのツールへ同じ引数は渡せないため）。
  - build / check / clean は追加引数を受け取りません（渡すとエラーで止まります）。
  - このリポジトリに CLI アプリはないため、run は gui の別名です。
  - check はこのリポジトリの canonical な品質ゲートをそのまま呼びます。
  - 並列テストや依存再生成など、ここに無い手順は AGENTS.md / README.md を参照してください。
"@
}

# 安全に削除できる生成物のみを対象にする。
# tmp\ 直下には利用者のファイルもあるため、tmp\ 全体は削除しない。
function Invoke-Clean {
    $targets = @(
        "build",
        "dist\OmniDesk",
        "dist\OmniDesk.zip",
        "dist\OmniDesk.exe",
        "dist\OmniDesk-onefile.exe",
        "tmp\pyinstaller-build",
        "tmp\pyinstaller-build-onefile",
        "tmp\coverage.xml",
        "tmp\.coverage",
        ".ruff_cache",
        ".pytest_cache",
        ".coverage",
        "coverage.xml"
    )

    foreach ($relative in $targets) {
        $path = Join-Path $RepoRoot $relative
        if (Test-Path -LiteralPath $path) {
            Write-Host "削除: $relative"
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
        }
    }

    # .venv と .git を除いた __pycache__ を削除する。
    Get-ChildItem -LiteralPath $RepoRoot -Directory -Recurse -Filter "__pycache__" -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch "\\.venv\\" -and $_.FullName -notmatch "\\.git\\" } |
        ForEach-Object {
            Write-Host ("削除: " + $_.FullName.Substring($RepoRoot.Length).TrimStart("\"))
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

    Write-Host "生成物を削除しました。" -ForegroundColor Green
}

Push-Location $RepoRoot
try {
    switch ($Command.ToLowerInvariant()) {
        "build" {
            if (Assert-NoExtraArguments -Name "build" -Arguments $Rest) {
                Write-Step "build (scripts\build-windows.ps1)"
                Invoke-RepoScript -Name "build-windows.ps1"
            }
        }
        { $_ -in @("run", "gui") } {
            Write-Step "gui (python -m omnidesk)"
            Invoke-Native -FilePath $Python -Arguments (@("-m", "omnidesk") + $Rest)
        }
        "test" {
            Write-Step "test (python -m pytest)"
            Invoke-Native -FilePath $Python -Arguments (@("-m", "pytest") + $Rest)
        }
        "lint" {
            Write-Step "ruff check"
            Invoke-Native -FilePath $Python -Arguments (@("-m", "ruff", "check", ".", "--no-cache") + $Rest)
            Write-Step "ruff format --check"
            Invoke-Native -FilePath $Python -Arguments @("-m", "ruff", "format", ".", "--check")
            Write-Step "pyright"
            Invoke-Native -FilePath $Python -Arguments @("-m", "pyright")
            if ($script:ExitCode -eq 0) {
                Write-Host "lint / 静的解析は成功しました。" -ForegroundColor Green
            }
        }
        "check" {
            if (Assert-NoExtraArguments -Name "check" -Arguments $Rest) {
                Write-Step "check (scripts\check.ps1)"
                Invoke-RepoScript -Name "check.ps1"
            }
        }
        "clean" {
            if (Assert-NoExtraArguments -Name "clean" -Arguments $Rest) {
                Write-Step "clean"
                Invoke-Clean
            }
        }
        { $_ -in @("help", "-h", "--help", "/?") } {
            Show-Help
        }
        default {
            Write-Host "不明なコマンド: $Command" -ForegroundColor Red
            Write-Host ""
            Show-Help
            $script:ExitCode = 2
        }
    }
} finally {
    Pop-Location
}

exit $script:ExitCode
