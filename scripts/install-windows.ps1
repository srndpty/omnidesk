param(
    [switch]$Build,
    [string]$Destination = (Join-Path $env:ProgramFiles "OmniDesk")
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function ConvertTo-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

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

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Source = Join-Path $RepoRoot "dist\OmniDesk"
$DestinationFullPath = [System.IO.Path]::GetFullPath($Destination)
$ExpectedProgramFilesRoot = [System.IO.Path]::GetFullPath($env:ProgramFiles)

if (-not (Test-ChildPath -Path $DestinationFullPath -Parent $ExpectedProgramFilesRoot)) {
    throw "Destination must be a child directory under Program Files: $DestinationFullPath"
}

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

Push-Location $RepoRoot
try {
    if ($Build) {
        Write-Host "Building OmniDesk..."
        & (Join-Path $PSScriptRoot "build-windows.ps1")
    }

    if (-not (Test-Path (Join-Path $Source "OmniDesk.exe"))) {
        throw "Build output was not found: $Source. Run build_windows.bat first, or pass -Build."
    }

    Write-Host "Installing from $Source"
    Write-Host "Installing to   $DestinationFullPath"

    if (Test-Path $DestinationFullPath) {
        Get-ChildItem -LiteralPath $DestinationFullPath -Force |
            Remove-Item -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path $DestinationFullPath | Out-Null
    }

    Remove-Item -LiteralPath "$DestinationFullPath.tmp" -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $Source -Force |
        Copy-Item -Destination $DestinationFullPath -Recurse -Force

    if (-not (Test-Path (Join-Path $DestinationFullPath "OmniDesk.exe"))) {
        throw "Install failed: OmniDesk.exe was not copied to $DestinationFullPath"
    }
    if (-not (Test-Path (Join-Path $DestinationFullPath "_internal"))) {
        throw "Install failed: _internal was not copied to $DestinationFullPath"
    }

    Write-Host "Installed OmniDesk to $DestinationFullPath"
} finally {
    Pop-Location
}
