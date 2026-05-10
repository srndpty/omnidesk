param(
    [switch]$Build,
    [string]$Destination = (Join-Path $env:ProgramFiles "OmniDesk")
)

$ErrorActionPreference = "Stop"

# 権限確認と引数クォート用の小さなヘルパー。
function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function ConvertTo-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

# インストール先の安全確認。宛先の中身を削除するため厳しめに判定する。
function Test-ChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Parent
    )

    $directorySeparators = @(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd($directorySeparators)
    $fullParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd($directorySeparators)
    if ([string]::Equals($fullPath, $fullParent, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $parentWithSeparator = $fullParent + [System.IO.Path]::DirectorySeparatorChar
    return $fullPath.StartsWith($parentWithSeparator, [StringComparison]::OrdinalIgnoreCase)
}

function Test-OmniDeskInstallDirectoryName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $directorySeparators = @(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $leafName = Split-Path -Leaf ([System.IO.Path]::GetFullPath($Path).TrimEnd($directorySeparators))
    return $leafName -eq "OmniDesk" -or $leafName.StartsWith("OmniDesk-", [StringComparison]::OrdinalIgnoreCase)
}

function Test-OnedirInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return (Test-Path (Join-Path $Path "OmniDesk.exe")) -and
        (Test-Path (Join-Path $Path "_internal"))
}

function Test-OmniDeskProcessInDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $directorySeparators = @(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $installRoot = [System.IO.Path]::GetFullPath($Path).TrimEnd($directorySeparators) +
        [System.IO.Path]::DirectorySeparatorChar

    foreach ($process in Get-Process -Name "OmniDesk" -ErrorAction SilentlyContinue) {
        try {
            $modulePath = [System.IO.Path]::GetFullPath($process.MainModule.FileName)
        } catch {
            return $true
        }
        if ($modulePath.StartsWith($installRoot, [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    Get-ChildItem -LiteralPath $Source -Force |
        Copy-Item -Destination $Destination -Recurse -Force
}

function Clear-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (Test-Path $Path) {
        Get-ChildItem -LiteralPath $Path -Force |
            Remove-Item -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Source = Join-Path $RepoRoot "dist\OmniDesk"
$DestinationFullPath = [System.IO.Path]::GetFullPath($Destination)
$ExpectedProgramFilesRoot = [System.IO.Path]::GetFullPath($env:ProgramFiles)

# 昇格を求める前に、広すぎる宛先や無関係な宛先を拒否する。
if (-not (Test-ChildPath -Path $DestinationFullPath -Parent $ExpectedProgramFilesRoot)) {
    throw "Destination must be a child directory under Program Files: $DestinationFullPath"
}
if (-not (Test-OmniDeskInstallDirectoryName -Path $DestinationFullPath)) {
    throw "Destination directory name must be OmniDesk or OmniDesk-*: $DestinationFullPath"
}

# Program Files へ書き込むため、未昇格なら管理者として再実行する。
if (-not (Test-Administrator)) {
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (ConvertTo-Argument $PSCommandPath),
        "-Destination",
        (ConvertTo-Argument $DestinationFullPath)
    )
    if ($Build) {
        $argsList += "-Build"
    }
    $process = Start-Process -FilePath "powershell" -ArgumentList $argsList -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

# 必要ならビルドし、検証済みのステージングからインストール先を置き換える。
Push-Location $RepoRoot
try {
    if ($Build) {
        Write-Host "Building OmniDesk..."
        & (Join-Path $PSScriptRoot "build-windows.ps1")
    }

    if (-not (Test-Path $Source -PathType Container)) {
        throw "Build output was not found: $Source. Run build_windows.bat first, or pass -Build."
    }
    if (-not (Test-Path (Join-Path $Source "OmniDesk.exe"))) {
        throw "Build output was not found: $Source. Run build_windows.bat first, or pass -Build."
    }
    if (-not (Test-Path (Join-Path $Source "_internal"))) {
        throw "Build output is incomplete: $Source\_internal was not found."
    }

    Write-Host "Installing from $Source"
    Write-Host "Installing to   $DestinationFullPath"

    $destinationParent = Split-Path -Parent $DestinationFullPath
    $destinationLeaf = Split-Path -Leaf $DestinationFullPath
    $replaceId = [System.Guid]::NewGuid().ToString("N")
    $stagingDestination = Join-Path $destinationParent "$destinationLeaf.installing.$replaceId"
    $backupDestination = Join-Path $destinationParent "$destinationLeaf.backup.$replaceId"

    if (Test-Path $DestinationFullPath -PathType Leaf) {
        throw "Destination exists but is not a directory: $DestinationFullPath"
    }
    if (Test-OmniDeskProcessInDirectory -Path $DestinationFullPath) {
        throw "OmniDesk is running from $DestinationFullPath. Close it before installing."
    }

    # 古いインストーラ方式で残った一時ディレクトリを掃除する。
    Remove-Item -LiteralPath "$DestinationFullPath.tmp" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stagingDestination -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backupDestination -Recurse -Force -ErrorAction SilentlyContinue

    New-Item -ItemType Directory -Path $stagingDestination | Out-Null
    Copy-DirectoryContents -Source $Source -Destination $stagingDestination

    # onedir 配置として使えないコピー結果なら、既存インストールを触る前に失敗させる。
    if (-not (Test-OnedirInstall -Path $stagingDestination)) {
        throw "Install failed: staging directory is incomplete: $stagingDestination"
    }

    $hasExistingInstall = Test-Path $DestinationFullPath -PathType Container
    $hasBackup = $false
    if ($hasExistingInstall) {
        New-Item -ItemType Directory -Path $backupDestination | Out-Null
        Copy-DirectoryContents -Source $DestinationFullPath -Destination $backupDestination
        $hasBackup = $true
    }

    try {
        Clear-DirectoryContents -Path $DestinationFullPath
        Copy-DirectoryContents -Source $stagingDestination -Destination $DestinationFullPath

        if (-not (Test-OnedirInstall -Path $DestinationFullPath)) {
            throw "Install failed: destination directory is incomplete: $DestinationFullPath"
        }
    } catch {
        if ($hasBackup) {
            Write-Warning "Install failed. Restoring previous install from $backupDestination"
            try {
                Clear-DirectoryContents -Path $DestinationFullPath
                Copy-DirectoryContents -Source $backupDestination -Destination $DestinationFullPath
            } catch {
                Write-Warning "Rollback failed. Backup remains at $backupDestination"
            }
        }
        throw
    }

    Remove-Item -LiteralPath $stagingDestination -Recurse -Force -ErrorAction SilentlyContinue
    if ($hasBackup) {
        Remove-Item -LiteralPath $backupDestination -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Installed OmniDesk to $DestinationFullPath"
} finally {
    Pop-Location
}
