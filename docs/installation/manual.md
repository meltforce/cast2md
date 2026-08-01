# Manual Installation

Install cast2md directly with Python for development or customized deployments.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager (recommended) or pip
- PostgreSQL 15+ with pgvector extension
- ffmpeg

## Setup

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and Install

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md
uv sync --frozen
```

### 3. Set Up PostgreSQL

You can run PostgreSQL via Docker or install it natively.

=== "Docker (Recommended)"

    ```bash
    docker compose up -d postgres
    ```

    This starts PostgreSQL with pgvector on port 5432.

=== "Native Install"

    Install PostgreSQL and the pgvector extension for your platform, then create the database:

    ```sql
    CREATE USER cast2md WITH PASSWORD 'your_password';
    CREATE DATABASE cast2md OWNER cast2md;
    \c cast2md
    CREATE EXTENSION vector;
    ```

### 4. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
DATABASE_URL=postgresql://cast2md:your_password@localhost:5432/cast2md
STORAGE_PATH=./data/podcasts
TEMP_DOWNLOAD_PATH=./data/temp
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

### 5. Initialize and Run

```bash
# Initialize the database
uv run cast2md init-db

# Start the server
uv run cast2md serve
```

The server starts at `http://localhost:8000`.

## Development Mode

For development with auto-reload:

```bash
uv run cast2md serve --reload
```

## Running with pip

If you prefer pip over uv:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

cast2md init-db
cast2md serve
```

## Optional Dependencies

| Extra | Install | Purpose |
|-------|---------|---------|
| `dev` | `uv sync --extra dev` | Testing and linting (pytest, ruff, mkdocs) |
| `mlx` | `uv sync --extra mlx` | Apple Silicon MLX Whisper backend |
| `node` | `pip install cast2md[node]` | Minimal install for transcriber nodes |

## Updating

```bash
git pull
uv sync --frozen

# If dependencies changed
uv pip install -e .

# If database schema changed (auto-migrates on start)
uv run cast2md serve
```
