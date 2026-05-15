---
title: Installation
sidebar_position: 1
---

# Installation

Waldo runs as Docker containers. The installer detects your OS, installs the
prerequisites it needs (Docker, uv, Node.js, NVIDIA Container Toolkit), writes
`.env`, optionally downloads the SAM 3.1 weights, and brings the stack up.

## One-shot install

### macOS, Linux, WSL

```bash
curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/oldhero5/waldo/main/install.ps1 | iex
```

### Windows (cmd.exe)

```cmd
curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.cmd -o install.cmd && install.cmd && del install.cmd
```

The Windows wrappers verify (and install, when needed) WSL2 + Docker Desktop,
then hand off to `install.sh` inside Ubuntu. WSL2 is where Waldo actually runs
on Windows — the GPU drivers in WSL come from the Windows host driver
automatically.

When the installer finishes, open [http://localhost:8000](http://localhost:8000).
The first-run admin password is in the app logs:

```bash
docker compose logs app | grep -A 2 "bootstrapped first admin"
```

## What the installer does

1. **Locates or clones the repo.** If you ran it from inside a clone, it uses
   that. Otherwise it clones to `~/waldo` (override with `--dir`).
2. **Detects your platform and GPU.** Prints what it found (OS, package
   manager, NVIDIA driver, container toolkit). On macOS Apple Silicon it picks
   the MLX/MPS path; on Linux/WSL with NVIDIA it picks the CUDA path; otherwise
   it falls back to CPU.
3. **Installs prerequisites** (skip with `--skip-prereqs`):
   - Docker Engine (Linux) — uses `get.docker.com` if you have sudo
   - [uv](https://docs.astral.sh/uv/) — Python toolchain
   - Node.js 20+
   - `nvidia-container-toolkit` (Linux + NVIDIA GPU only)
4. **Writes `.env`** from `.env.example`, sets `DEVICE` and `DTYPE` to match
   the GPU it picked, and prompts for `HF_TOKEN` (skip-able in `--yes` mode).
5. **Verifies GPU passthrough** by running `nvidia-smi` inside a CUDA
   container. If passthrough fails, it falls back to CPU rather than starting
   workers that can't see the GPU.
6. **Downloads SAM 3.1 weights** via `scripts/download_models.sh` (skip with
   `--skip-models`).
7. **Brings the stack up:**
   - Linux / WSL → `docker compose --profile nvidia up -d --build` (or
     `--profile apple` on CPU)
   - macOS Apple Silicon → `make up-mac` (infra in Docker, MLX workers
     native so they reach MPS)
   - Skip with `--skip-up` to configure only.

## Installer flags

```
--dir PATH                 Where to clone the repo if needed (default: ~/waldo)
--branch NAME              Branch to clone (default: main)
--repo URL                 Git URL to clone from
--skip-prereqs             Don't install Docker/uv/Node
--skip-models              Don't download SAM 3.1 weights
--skip-up                  Don't run docker compose up
--cpu                      Force CPU even if a GPU is detected
--gpu nvidia|apple|none    Override GPU detection
--yes                      Non-interactive
--no-color                 Disable colored output
```

You can pipe flags through `curl`:

```bash
curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.sh \
  | bash -s -- --dir ~/projects/waldo --skip-models --yes
```

## Prerequisites (manual install)

If you'd rather install everything yourself:

| Platform | Requirement |
| --- | --- |
| Linux | Docker Engine 24+, Docker Compose v2 |
| macOS (Apple Silicon) | Docker Desktop 4.30+ or [OrbStack](https://orbstack.dev/) |
| Windows | Docker Desktop 4.30+ with WSL 2 backend |
| GPU (optional) | NVIDIA Container Toolkit (Linux only — WSL2 inherits from the Windows driver) |
| Local dev | Node.js 20+, [uv](https://docs.astral.sh/uv/) |
| Models | [HuggingFace token](https://huggingface.co/settings/tokens) for SAM 3.1 |

Then:

```bash
git clone https://github.com/oldhero5/waldo.git
cd waldo
cp .env.example .env       # set HF_TOKEN, optionally tweak DEVICE
make up                    # auto-routes by OS
# or: docker compose --profile nvidia up -d --build
```

## NVIDIA: the gotchas

- **WSL2 + NVIDIA**: install the NVIDIA driver on **Windows**, not inside WSL.
  CUDA inside WSL is provided by the Windows driver automatically. Installing
  a Linux NVIDIA driver inside WSL will break things.
- **Linux + NVIDIA**: `nvidia-container-toolkit` must be installed and Docker
  must be restarted after `nvidia-ctk runtime configure --runtime=docker`.
  The installer does this for you on apt/dnf.
- **PyTorch**: PyPI's default `torch` is CPU-only. Waldo's `Dockerfile.nvidia`
  installs from `download.pytorch.org/whl/cu124` so the in-container PyTorch
  has CUDA. If `torch.cuda.is_available()` is `False`, you almost certainly
  have a CPU wheel — rebuild the image.
- **Verify it from outside**: `make gpu-check` runs `nvidia-smi` in a fresh
  CUDA container, the same way the installer does.

## Production setup

For production, set `APP_ENV=production` and secure values for:

- `JWT_SECRET` (use `openssl rand -hex 32`)
- `POSTGRES_PASSWORD`
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
- `ADMIN_BOOTSTRAP_PASSWORD` (the random fallback is dev-only)

The app refuses to start if any of these are still on insecure defaults. See
[Security](../architecture/security) for the full hardening checklist.
