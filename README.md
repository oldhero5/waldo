# Waldo

> **Where's Waldo? Right here, finding objects in your video.**

Self-hosted ML platform for video object detection at scale. Auto-label any object in video using text prompts or click-based exemplars (powered by [SAM 3](https://huggingface.co/facebook/sam3)), train YOLO26 detectors on the labeled data, monitor training live, and deploy the model to a serving endpoint or edge device.

📘 **Full documentation:** [`docs-site/`](docs-site/) — quickstart, UI tour with screenshots and short walkthrough videos, API reference, deployment guides. Build it locally with `cd docs-site && npm install && npm run start`.

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │              React UI (SPA)              │
                    └──────────────┬───────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────────┐
                    │          FastAPI  (waldo-app)            │
                    │   REST API  ·  WebSocket  ·  Static UI   │
                    └──┬───────────────┬───────────────┬───────┘
                       │               │               │
              ┌────────▼──┐    ┌───────▼───┐    ┌──────▼──────┐
              │  Celery   │    │  Celery   │    │             │
              │  Labeler  │    │  Trainer  │    │   Infra     │
              │  (SAM 3)  │    │  (YOLO)   │    │             │
              └───────────┘    └───────────┘    │ PostgreSQL  │
                                                │ Redis       │
                                                │ MinIO       │
                                                └─────────────┘
```

**Pipeline:** Upload video → Label (text or click) → Review → Train YOLO → Deploy API

## Quickstart

One command. The installer detects your platform and GPU, installs missing
dependencies (Docker, uv, Node.js, NVIDIA Container Toolkit), writes `.env`,
downloads the SAM 3.1 weights, and brings the stack up.

**macOS, Linux, WSL:**

```bash
curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/oldhero5/waldo/main/install.ps1 | iex
```

**Windows (cmd.exe):**

```cmd
curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.cmd -o install.cmd && install.cmd && del install.cmd
```

The Windows wrappers install/verify WSL2 + Docker Desktop, then hand off to
`install.sh` inside Ubuntu — that's where Waldo actually runs.

After it finishes, open **[http://localhost:8000](http://localhost:8000)**. The
first-run admin password lives in the app logs:

```bash
docker compose logs app | grep -A 2 "bootstrapped first admin"
```

### Already cloned the repo?

```bash
git clone https://github.com/oldhero5/waldo.git && cd waldo
./install.sh                 # or: ./install.sh --skip-up --skip-models for a dry config
```

### Installer flags

| Flag | Default | What it does |
|------|---------|--------------|
| `--dir PATH` | `~/waldo` | Where to clone if Waldo isn't already on disk |
| `--branch NAME` | `main` | Branch to clone |
| `--cpu` | off | Force CPU even if a GPU is detected |
| `--gpu nvidia\|apple\|none` | auto | Override GPU detection |
| `--skip-prereqs` | off | Don't install Docker/uv/Node — assume present |
| `--skip-models` | off | Don't download SAM 3.1 weights |
| `--skip-up` | off | Don't run `docker compose up` — config only |
| `--yes` | off | Non-interactive (HF_TOKEN can be set later in `.env`) |

### Manual setup (for the curious)

The installer is the recommended path, but Waldo is just `docker-compose.yml`
underneath. If you want to drive it by hand:

```bash
git clone https://github.com/oldhero5/waldo.git && cd waldo
cp .env.example .env                     # add HF_TOKEN
make up                                  # auto-routes by OS
```

| Platform | Command | Workers in Docker? | GPU |
|----------|---------|:---:|-----|
| macOS (CPU) | `make up` | ✅ | none |
| macOS (native MPS) | `make up-mac` | ❌ native | Apple MPS |
| Linux + NVIDIA | `make up PROFILE=nvidia` | ✅ | CUDA |
| Linux (CPU only) | `make up` | ✅ | none |
| Windows (WSL 2) + NVIDIA | `make up PROFILE=nvidia` | ✅ | CUDA |

### macOS (Apple Silicon)

Apple's MPS cannot be passed through to Linux containers. Two options:

```bash
make up          # Everything in Docker, CPU workers. Slowest but zero setup.
make up-mac      # Infra + app in Docker, native MPS workers. Recommended.
```

`make up-mac` starts the labeler and trainer natively so they can reach the
M-series GPU. Logs land in `/tmp/waldo-labeler.log` and `/tmp/waldo-trainer.log`.
(`make up-gpu` is kept as a back-compat alias.)

### Linux with NVIDIA CUDA

The installer handles the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on apt/dnf systems. If you'd rather drive it manually:

```bash
# 1. Driver + toolkit (Ubuntu example — see NVIDIA docs for others)
sudo apt install -y nvidia-driver-550
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 2. Verify the GPU is visible to Docker
make gpu-check

# 3. Start Waldo with the nvidia profile
make up PROFILE=nvidia

# 4. Confirm the workers see the GPU inside the container
make gpu-logs
```

The `nvidia` profile builds `labeler/Dockerfile.nvidia` and `trainer/Dockerfile.nvidia`
from `nvidia/cuda:12.4.0-devel-ubuntu22.04`, installs CUDA-enabled PyTorch from
`download.pytorch.org/whl/cu124` (PyPI ships CPU-only torch by default — a common
silent-failure trap), and loads SAM 3 via PyTorch + Transformers. The Apple path
uses MLX, which is macOS-only. Training uses CUDA bf16 for speed.

Each nvidia worker runs `scripts/entrypoint-worker.sh` on boot, which prints
`nvidia-smi` + `torch.cuda.is_available()` so `make gpu-logs` immediately shows
whether passthrough is working. Compose also sets `shm_size: 4gb` so PyTorch
dataloader IPC doesn't OOM on the 64 MB Docker default.

### Windows with NVIDIA CUDA (via WSL 2)

Windows GPU support goes through **WSL 2 + Docker Desktop**. The NVIDIA driver
lives on the Windows host; CUDA inside WSL is provided by the driver
automatically — do **not** install a Linux CUDA driver inside WSL.

The PowerShell installer (`install.ps1`) handles WSL2 + Docker Desktop and then
hands off to `install.sh` inside Ubuntu. If you'd prefer to drive it manually,
the chain is the same: install the Windows NVIDIA driver, install WSL2 +
Ubuntu, install Docker Desktop with WSL integration, then run the Linux
installer (or `make up PROFILE=nvidia`) inside Ubuntu.

**Windows gotchas:**

- **Keep the repo in the WSL filesystem** (`~/waldo`, not `/mnt/c/...`). Cross-filesystem
  I/O is 10–20× slower, and ffmpeg frame extraction becomes the bottleneck.
- **Line endings**: configure git to keep LF (`git config --global core.autocrlf input`)
  so shell scripts don't break.
- **File watcher limits**: if `make dev-ui` misses changes, raise the inotify limit:
  `echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf && sudo sysctl -p`
- **Memory**: Docker Desktop defaults to half of host RAM. Training YOLO on 720p video
  wants ≥ 16 GB allocated — adjust in `%UserProfile%\.wslconfig`:
  ```
  [wsl2]
  memory=24GB
  processors=8
  ```
  then `wsl --shutdown` and restart.

Waldo has **not** been validated against native Windows (no WSL). Don't try
it — Ultralytics, Celery's solo pool, and ffmpeg all behave differently there.

### Linux without a GPU (CPU only)

```bash
make up          # Uses the apple profile; it's CPU-only Dockerfiles
```

Works for small datasets and smoke tests. Don't expect to train on real video.

### Verify it's running

```
http://localhost:8000    # Waldo UI
http://localhost:9001    # MinIO console  (minioadmin / minioadmin)
```

The app runs database migrations automatically on startup.

## Common commands

```bash
make up              # Start everything
make logs            # Tail all containers
make down            # Stop everything
make dev-ui          # Vite dev server with hot reload (proxies API)
make build-ui        # Production build → app/static/
make migrate         # Run Alembic migrations
make test            # Python test suite
make download-models # Download SAM 3 weights
```

## Web UI

| Page | Path | Description |
|------|------|-------------|
| Upload | `/upload` | Drag-and-drop video upload |
| Label | `/label/:videoId` | Text search + click mode, 5 task types |
| Review | `/review/:jobId` | Annotation reviewer with hotkeys |
| Train | `/train/:jobId` | Variant picker, hyperparameters, live metrics |
| Deploy | `/deploy` | Endpoints, test console, model registry, monitoring |

## API

All endpoints live under `/api/v1` and are documented at `/docs` (OpenAPI). Highlights:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/upload` | Upload video |
| `POST` | `/label` | Start text-prompt labeling |
| `POST` | `/label/exemplar` | Start click-based labeling |
| `GET` | `/status/{job_id}` | Job status + result URL |
| `POST` | `/train` | Start training run |
| `GET` | `/train/{run_id}` | Status + metrics + loss history |
| `WS` | `/ws/training/{run_id}` | Live metrics stream |
| `POST` | `/predict/image?model_id=ID` | Image inference |
| `POST` | `/predict/video?model_id=ID` | Video inference with tracking |
| `GET` | `/models` | List trained models |

### Example pipeline

```bash
# 1. Upload video
curl -X POST http://localhost:8000/api/v1/upload -F "file=@clip.mp4"

# 2. Label with a text prompt
curl -X POST http://localhost:8000/api/v1/label \
  -H "Content-Type: application/json" \
  -d '{"video_id": "VIDEO_ID", "text_prompt": "person", "task_type": "segment"}'

# 3. Poll until the labeling job finishes
curl http://localhost:8000/api/v1/status/JOB_ID

# 4. Train a YOLO model on the dataset
curl -X POST http://localhost:8000/api/v1/train \
  -H "Content-Type: application/json" \
  -d '{"job_id": "JOB_ID", "model_variant": "yolo26n-seg", "hyperparameters": {"epochs": 50}}'

# 5. Watch training (or open the Train page in the UI)
curl http://localhost:8000/api/v1/train/RUN_ID

# 6. Run inference against the trained model
curl -X POST "http://localhost:8000/api/v1/predict/image?model_id=MODEL_ID" \
  -H "Authorization: Bearer wld_YOUR_KEY" \
  -F "file=@test.jpg"
```

## YOLO task types

SAM 3 always outputs segmentation masks. Waldo's converters reshape them into whatever
format the selected YOLO task needs:

| Task | Output | YOLO variants |
|------|--------|---------------|
| Segmentation | Polygon vertices | `yolo26n-seg` → `yolo26x-seg` |
| Detection | Bounding boxes | `yolo26n` → `yolo26x` |
| Classification | Cropped images in class dirs | `yolo26n-cls` → `yolo26x-cls` |
| OBB | 4 rotated corner points | `yolo26n-obb` → `yolo26m-obb` |
| Pose | Bbox + centroid keypoint | `yolo26n-pose` → `yolo26m-pose` |

## Configuration

Everything comes from environment variables. See `.env.example`.

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVICE` | `mps` | `mps` (Apple), `cuda` (NVIDIA), or `cpu` |
| `DTYPE` | `float32` | `float32`, `bfloat16`, `float16` |
| `SAM3_MODEL_ID` | `facebook/sam3.1` | HuggingFace transformers model (Linux/CUDA) |
| `SAM3_MLX_MODEL_ID` | `mlx-community/sam3.1-bf16` | MLX variant (macOS) |
| `HF_TOKEN` | — | HuggingFace token for model download |
| `SLACK_WEBHOOK_URL` | — | Training alerts |
| `NTFY_TOPIC` | — | Push notifications |

## Testing

```bash
# Unit tests (no infra)
uv run pytest tests/test_converters.py tests/test_frame_extractor.py -v

# Trainer tests (Redis + MinIO)
uv run pytest tests/test_trainer.py -v

# API tests (full stack running)
uv run pytest tests/test_api.py tests/test_api_extended.py -v

# Everything
make test && cd ui && npx playwright test
```

## Project structure

```
waldo/
├── app/                    # FastAPI application
│   ├── main.py             # Entrypoint, routers, SPA fallback
│   ├── api/                # Route handlers
│   └── static/             # Built React UI
├── labeler/                # SAM 3 labeling pipeline
│   ├── sam3_engine.py      # PyTorch path (Linux/CUDA)
│   ├── video_labeler.py    # MLX path (macOS)
│   ├── text_labeler.py     # Text-prompt flow
│   ├── frame_extractor.py  # ffmpeg extraction + phash dedup
│   └── converters/         # Mask → YOLO format converters
├── trainer/                # YOLO training pipeline
│   ├── train_manager.py    # Ultralytics orchestrator
│   ├── dataset_builder.py  # Dataset prep from DB
│   ├── metrics_streamer.py # Redis pub/sub for live metrics
│   └── exporter.py         # ONNX, TFLite, CoreML export
├── lib/                    # Shared library
│   ├── config.py           # Pydantic settings
│   ├── db.py               # SQLAlchemy models
│   ├── storage.py          # MinIO client
│   └── tasks.py            # Celery tasks
├── ui/                     # React 19 + Vite + TypeScript + Tailwind
├── alembic/                # Database migrations
├── scripts/                # Setup + maintenance scripts
├── tests/                  # Python test suite
└── docker-compose.yml      # All services, apple + nvidia profiles
```

## Documentation

The full docs live in [`docs-site/`](docs-site/) (Docusaurus). Highlights:

- **[Quickstart](docs-site/docs/getting-started/quickstart.md)** — upload a clip, auto-label, train, deploy in ~15 minutes
- **[UI Tour](docs-site/docs/ui/overview.md)** — every page screenshotted, with short walkthrough videos
- **[Architecture](docs-site/docs/architecture/overview.md)** — services, data model, security
- **[API Reference](docs-site/docs/api/overview.md)** — every REST endpoint grouped by resource
- **[Workflow Blocks](docs-site/docs/workflows/overview.md)** — composable blocks for the visual editor
- **[Deployment](docs-site/docs/deployment/docker.md)** — Docker, Linux, Windows, and edge devices

Run the site locally:

```bash
cd docs-site
npm install
npm run start          # http://localhost:3000
```

Or build static HTML with `npm run build`. Screenshots and videos can be regenerated against your local Waldo with `npm run screenshots` and `npx playwright test scripts/recordings.spec.ts`.

## License

See [LICENSE](LICENSE).
