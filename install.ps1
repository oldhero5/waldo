# Waldo one-shot installer for Windows (PowerShell).
#
# Usage:
#   irm https://raw.githubusercontent.com/oldhero5/waldo/main/install.ps1 | iex
#   .\install.ps1 [-Branch main] [-Repo URL] [-Dir PATH] [-SkipUp] [-SkipModels] [-Yes]
#
# What this does on Windows:
#   1. Verifies / installs WSL2 + Ubuntu (uses winget when available, otherwise
#      falls back to `wsl --install -d Ubuntu`).
#   2. Reminds you to install Docker Desktop with the WSL2 backend (and the
#      NVIDIA driver, if you have a GPU). These need a Windows reboot, so we
#      can't fully automate them.
#   3. Hands off to install.sh inside Ubuntu/WSL — that's where the real
#      stack lives. The Linux installer is the source of truth.

[CmdletBinding()]
param(
    [string]$Repo   = "https://github.com/oldhero5/waldo.git",
    [string]$Branch = "main",
    [string]$Dir    = "",          # path inside WSL, e.g. /home/USER/waldo. Default: ~/waldo.
    [string]$Distro = "Ubuntu",
    [switch]$SkipPrereqs,
    [switch]$SkipModels,
    [switch]$SkipUp,
    [switch]$Cpu,
    [string]$Gpu    = "",          # nvidia | apple | none
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   ok $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "   ! $msg"  -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "   x $msg"  -ForegroundColor Red }
function Fail($msg)       { Write-Err2 $msg; exit 1 }

@"

   _       __      __    __
  | |     / /___ _/ /___/ /___
  | | /| / / __ ``/ / __  / __ \
  | |/ |/ / /_/ / / /_/ / /_/ /
  |__/|__/\__,_/_/\__,_/\____/

  Self-hosted ML for video object detection.
  Installer for Windows (WSL2 + Docker Desktop).

"@ | Write-Host

# ── Step 1: WSL2 ────────────────────────────────────────────────
Write-Step "Checking WSL2"

$wslOk = $false
try {
    $null = & wsl.exe --status 2>$null
    if ($LASTEXITCODE -eq 0) { $wslOk = $true }
} catch { $wslOk = $false }

if (-not $wslOk) {
    Write-Warn2 "WSL2 is not installed. Running 'wsl --install -d $Distro' (requires admin)…"
    Write-Warn2 "Windows will reboot afterwards. After reboot, finish setting up your Ubuntu user, then re-run this installer."
    if (-not $Yes) {
        $confirm = Read-Host "   Proceed with WSL install? [y/N]"
        if ($confirm -notmatch '^(y|yes)$') { Fail "Aborted." }
    }
    Start-Process -FilePath "wsl.exe" -ArgumentList @("--install","-d",$Distro) -Verb RunAs -Wait
    Write-Host "After Windows reboots and Ubuntu finishes initial setup, re-run this script."
    exit 0
}
Write-Ok  "WSL2 present"

# Make sure the chosen distro exists.
$distros = (& wsl.exe -l -q) -replace "`0","" | Where-Object { $_ -and $_.Trim() }
if ($distros -notcontains $Distro) {
    Write-Warn2 "$Distro distro not installed — running 'wsl --install -d $Distro'"
    Start-Process -FilePath "wsl.exe" -ArgumentList @("--install","-d",$Distro) -Verb RunAs -Wait
    Fail "Finish $Distro first-run setup, then re-run this script."
}

# ── Step 2: Docker Desktop ──────────────────────────────────────
Write-Step "Checking Docker Desktop"

$dockerExe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
$dockerCli = "docker.exe"
$haveDocker = (Test-Path $dockerExe) -or (Get-Command $dockerCli -ErrorAction SilentlyContinue)

if (-not $haveDocker) {
    Write-Warn2 "Docker Desktop not found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if ($Yes -or (Read-Host "   Install Docker Desktop via winget? [Y/n]") -notmatch '^(n|no)$') {
            winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
            Write-Warn2 "Docker Desktop installed. Start it once, enable WSL integration for $Distro, then re-run this script."
            exit 0
        }
    }
    Fail "Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and enable WSL integration for $Distro, then re-run."
}
Write-Ok  "Docker Desktop present"

# Check the daemon is running
$dockerInfo = & docker.exe info 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "Docker Desktop is installed but not running. Starting it…"
    Start-Process -FilePath $dockerExe -ErrorAction SilentlyContinue
    Write-Host "   Waiting for Docker daemon (up to 60s)…"
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        & docker.exe info > $null 2>&1
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker daemon didn't come up. Open Docker Desktop manually, enable WSL integration for $Distro, then re-run."
    }
}
Write-Ok  "Docker daemon is running"

# Verify WSL integration for our distro.
$wslIntegration = & docker.exe context ls --format "{{.Name}}" 2>$null
if ($wslIntegration -notcontains "default") {
    Write-Warn2 "Could not confirm Docker WSL integration. In Docker Desktop → Settings → Resources → WSL Integration, toggle on $Distro."
}

# ── Step 3: NVIDIA hint ─────────────────────────────────────────
Write-Step "Checking NVIDIA GPU (host)"
$nvidiaOk = $false
try {
    $smi = & nvidia-smi.exe -L 2>$null
    if ($LASTEXITCODE -eq 0 -and $smi) {
        Write-Ok "NVIDIA driver detected on Windows host"
        $smi | Select-Object -First 4 | ForEach-Object { Write-Host "      $_" }
        $nvidiaOk = $true
    }
} catch { $nvidiaOk = $false }
if (-not $nvidiaOk) {
    Write-Warn2 "No NVIDIA driver detected. Waldo will run on CPU."
    Write-Host  "   For GPU support, install the latest NVIDIA Game-Ready or Studio driver"
    Write-Host  "   on Windows: https://www.nvidia.com/Download/index.aspx"
    Write-Host  "   (Do NOT install a Linux NVIDIA driver inside WSL — the Windows driver provides CUDA to WSL automatically.)"
}

# ── Step 4: hand off to install.sh inside WSL ───────────────────
Write-Step "Handing off to install.sh inside $Distro"

$flags = @()
if ($SkipPrereqs) { $flags += "--skip-prereqs" }
if ($SkipModels) { $flags += "--skip-models" }
if ($SkipUp)     { $flags += "--skip-up" }
if ($Cpu)        { $flags += "--cpu" }
if ($Gpu)        { $flags += "--gpu"; $flags += $Gpu }
if ($Yes)        { $flags += "--yes" }
if ($Repo)       { $flags += "--repo"; $flags += $Repo }
if ($Branch)     { $flags += "--branch"; $flags += $Branch }
if ($Dir)        { $flags += "--dir"; $flags += $Dir }

$flagStr = ($flags | ForEach-Object { "'$_'" }) -join ' '

# Bootstrap-and-run inside WSL. We feed the installer through bash -s --
# so `curl | bash` semantics work even on a fresh distro that doesn't yet
# have the repo cloned.
$bashCmd = "set -e; curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/$Branch/install.sh | bash -s -- $flagStr"

Write-Host "   Running inside WSL ($Distro):"
Write-Host "   $bashCmd"
& wsl.exe -d $Distro -- bash -lc $bashCmd
if ($LASTEXITCODE -ne 0) { Fail "Installer inside WSL exited with code $LASTEXITCODE" }

Write-Step "Done"
Write-Host ""
Write-Host "  Waldo is installed inside WSL ($Distro)."  -ForegroundColor Green
Write-Host "  Web UI:    http://localhost:8000"          -ForegroundColor Cyan
Write-Host "  MinIO:     http://localhost:9001  (minioadmin / minioadmin)" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Open a WSL shell with:  wsl -d $Distro"
Write-Host "  Then:  cd ~/waldo  &&  make logs"
Write-Host ""
