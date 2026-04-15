---
title: Linux
sidebar_position: 2
---

# Linux Deployment

Tested on Ubuntu 22.04 LTS and Debian 12. Other distros work; install commands differ.

## Prerequisites

```bash
# Docker Engine (official repo, not the distro version)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Docker Compose v2 ships with the engine on this install path
docker compose version
```

## Optional: NVIDIA GPU support

```bash
# NVIDIA Container Toolkit
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo systemctl restart docker

# Verify
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

## Run Waldo

```bash
git clone https://github.com/your-org/waldo.git
cd waldo
cp .env.example .env
# Edit .env to set passwords + APP_ENV=production
docker compose --profile nvidia up -d
```

## Reverse proxy

For HTTPS, terminate at Caddy or Nginx. Caddyfile example:

```caddy
waldo.example.com {
    reverse_proxy localhost:8000
}
```

Caddy will auto-provision a Let's Encrypt cert.

## Systemd

To start Waldo on boot, use a tiny systemd unit:

```ini
# /etc/systemd/system/waldo.service
[Unit]
Description=Waldo
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/waldo
ExecStart=/usr/bin/docker compose --profile nvidia up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now waldo
```
