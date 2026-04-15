---
title: Windows
sidebar_position: 3
---

# Windows Deployment

Waldo runs on Windows via Docker Desktop with the WSL 2 backend. There is no native Windows path — every service runs inside a Linux container.

## Prerequisites

1. **Windows 10 22H2 / Windows 11** with virtualization enabled in BIOS.
2. **WSL 2** — `wsl --install` (requires admin PowerShell).
3. **Docker Desktop** — install from <https://www.docker.com/products/docker-desktop>. Enable "Use the WSL 2 based engine" in Settings → General.
4. **Git for Windows** — install from <https://git-scm.com>.

## Optional: NVIDIA GPU passthrough

Requires a Windows 11 host with NVIDIA driver 535+. Docker Desktop picks up the GPU automatically once the Windows-side driver is installed; no extra container toolkit needed inside WSL.

Verify:
```powershell
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

## Clone and run

Use **PowerShell** (not cmd.exe). All paths are forward-slashed because Docker mounts go through WSL.

```powershell
git clone https://github.com/your-org/waldo.git
cd waldo
copy .env.example .env
notepad .env   # set passwords, APP_ENV=production
docker compose --profile nvidia up -d
```

Browse to <http://localhost:8000>.

## File system performance

Mount Waldo from the **WSL filesystem**, not from `C:\`. Cross-OS file watching across the WSL boundary is slow:

```powershell
wsl
cd ~
git clone https://github.com/your-org/waldo.git
cd waldo
docker compose --profile nvidia up -d
```

Then access from Windows via `\\wsl$\Ubuntu\home\<user>\waldo`.

## Pre-commit hooks on Windows

Run them inside Docker — no Python or Node required on Windows:

```powershell
docker compose -f docker-compose.precommit.yml run --rm precommit
```

This is the same workflow as Linux.
