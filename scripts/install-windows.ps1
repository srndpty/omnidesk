param(
    [switch]$Build,
    [string]$Destination = (Join-Path $env:ProgramFiles "OmniDesk")
)

$ErrorActionPreference = "Stop"

# 権限確認と引数クォート用の小さなヘルパー。
function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal -ArgumentList $identity
    return ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
}

function ConvertTo-Argument([string]$Value) {
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return ('"' + $escaped + '"')
}

function ConvertTo-FullDirectoryPath([string]$Path) {
    $directorySeparators = @(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    return ([System.IO.Path]::GetFullPath($Path).TrimEnd($directorySeparators))
}

# インストール先の安全確認。宛先の中身を削除するため厳しめに判定する。
function Test-DirectChildPath {
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
    $actualParent = Split-Path -Parent $fullPath
    return ([string]::Equals($actualParent, $fullParent, [StringComparison]::OrdinalIgnoreCase))
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
    return (
        $leafName -eq "OmniDesk" -or
        $leafName.StartsWith("OmniDesk-", [StringComparison]::OrdinalIgnoreCase)
    )
}

function Test-OnedirInstall {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return (
        (Test-Path -LiteralPath (Join-Path $Path "OmniDesk.exe") -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path "_internal") -PathType Container)
    )
}

function Test-DirectoryEmpty {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $firstChild = Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop | Select-Object -First 1
    return ($null -eq $firstChild)
}

function Test-ReparsePoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    return ($item -and (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))
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
    $installRoot = (
        [System.IO.Path]::GetFullPath($Path).TrimEnd($directorySeparators) +
        [System.IO.Path]::DirectorySeparatorChar
    )

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

    if (Test-Path -LiteralPath $Path) {
        Get-ChildItem -LiteralPath $Path -Force |
            Remove-Item -Recurse -Force
    } else {
        [System.IO.Directory]::CreateDirectory($Path) | Out-Null
    }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Source = Join-Path $RepoRoot "dist\OmniDesk"
$DestinationFullPath = ConvertTo-FullDirectoryPath $Destination
$ExpectedProgramFilesRoot = ConvertTo-FullDirectoryPath $env:ProgramFiles

# 昇格を求める前に、広すぎる宛先や無関係な宛先を拒否する。
if (-not (Test-DirectChildPath -Path $DestinationFullPath -Parent $ExpectedProgramFilesRoot)) {
    throw "Destination must be a direct child directory under Program Files: $DestinationFullPath"
}
if (-not (Test-OmniDeskInstallDirectoryName -Path $DestinationFullPath)) {
    throw "Destination directory name must be OmniDesk or OmniDesk-*: $DestinationFullPath"
}

# ビルドは通常ユーザーの venv/PATH で実行し、コピーだけを昇格する。
if ($Build) {
    Push-Location $RepoRoot
    try {
        Write-Host "Building OmniDesk..."
        & (Join-Path $PSScriptRoot "build-windows.ps1")
    } finally {
        Pop-Location
    }
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
    $process = Start-Process -FilePath "powershell" -ArgumentList $argsList -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

# 検証済みのステージングからインストール先を置き換える。
Push-Location $RepoRoot
try {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Build output was not found: $Source. Run build_windows.bat first, or pass -Build."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Source "OmniDesk.exe") -PathType Leaf)) {
        throw "Build output was not found: $Source. Run build_windows.bat first, or pass -Build."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Source "_internal") -PathType Container)) {
        throw "Build output is incomplete: $Source\_internal was not found."
    }

    Write-Host "Installing from $Source"
    Write-Host "Installing to   $DestinationFullPath"

    $destinationParent = Split-Path -Parent $DestinationFullPath
    $destinationLeaf = Split-Path -Leaf $DestinationFullPath
    $replaceId = [System.Guid]::NewGuid().ToString("N")
    $stagingDestination = Join-Path $destinationParent "$destinationLeaf.installing.$replaceId"
    $backupDestination = Join-Path $destinationParent "$destinationLeaf.backup.$replaceId"

    if (Test-Path -LiteralPath $DestinationFullPath -PathType Leaf) {
        throw "Destination exists but is not a directory: $DestinationFullPath"
    }
    if (Test-ReparsePoint -Path $DestinationFullPath) {
        throw "Destination must not be a reparse point: $DestinationFullPath"
    }
    if (Test-OmniDeskProcessInDirectory -Path $DestinationFullPath) {
        throw "OmniDesk is running from $DestinationFullPath. Close it before installing."
    }

    $backupCreated = $false
    $backupReady = $false
    $keepBackup = $false

    try {
        # 今回の実行で使う一時ディレクトリだけを事前に掃除する。
        Remove-Item -LiteralPath $stagingDestination -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $backupDestination -Recurse -Force -ErrorAction SilentlyContinue

        [System.IO.Directory]::CreateDirectory($stagingDestination) | Out-Null
        Copy-DirectoryContents -Source $Source -Destination $stagingDestination

        # onedir 配置として使えないコピー結果なら、既存インストールを触る前に失敗させる。
        if (-not (Test-OnedirInstall -Path $stagingDestination)) {
            throw "Install failed: staging directory is incomplete: $stagingDestination"
        }

        $hasExistingInstall = Test-Path -LiteralPath $DestinationFullPath -PathType Container
        if ($hasExistingInstall) {
            if ((-not (Test-OnedirInstall -Path $DestinationFullPath)) -and
                (-not (Test-DirectoryEmpty -Path $DestinationFullPath))) {
                throw "Destination exists but does not look like an OmniDesk onedir install: $DestinationFullPath"
            }

            [System.IO.Directory]::CreateDirectory($backupDestination) | Out-Null
            $backupCreated = $true
            Copy-DirectoryContents -Source $DestinationFullPath -Destination $backupDestination
            $backupReady = $true
        }

        try {
            Clear-DirectoryContents -Path $DestinationFullPath
            Copy-DirectoryContents -Source $stagingDestination -Destination $DestinationFullPath

            if (-not (Test-OnedirInstall -Path $DestinationFullPath)) {
                throw "Install failed: destination directory is incomplete: $DestinationFullPath"
            }
        } catch {
            if ($backupReady) {
                Write-Warning "Install failed. Restoring previous install from $backupDestination"
                try {
                    Clear-DirectoryContents -Path $DestinationFullPath
                    Copy-DirectoryContents -Source $backupDestination -Destination $DestinationFullPath
                } catch {
                    $keepBackup = $true
                    Write-Warning "Rollback failed. Backup remains at $backupDestination"
                }
            }
            throw
        }

        Write-Host "Installed OmniDesk to $DestinationFullPath"
    } finally {
        Remove-Item -LiteralPath $stagingDestination -Recurse -Force -ErrorAction SilentlyContinue
        if ($backupCreated -and -not $keepBackup) {
            Remove-Item -LiteralPath $backupDestination -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

} finally {
    Pop-Location
}
