# Docker Installation

The recommended way to run the cast2md server in production. This installs everything needed for the full workflow -- episode downloading, transcription, search, and the web UI.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/) (included with Docker Desktop)

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/meltforce/cast2md.git
cd cast2md
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
POSTGRES_PASSWORD=your_secure_password
```

Optional settings:

```bash
PORT=8000
DATA_PATH=./data
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cpu
```

See [Environment Variables](../configuration/environment.md) for the full reference.

### 3. Start the Stack

```bash
docker compose up -d
```

This starts two containers:

- **cast2md** -- the application on port 8000
- **postgres** -- PostgreSQL with pgvector extension

### 4. Access the Web UI

Open `http://localhost:8000` in your browser.

## Docker Compose Configuration

The `compose.example.yml` provides a reference configuration. Key settings:

```yaml
services:
  cast2md:
    image: meltforce/cast2md:latest
    ports:
      - "${PORT:-8000}:8000"
    volumes:
      - ${DATA_PATH:-./data}:/data
    environment:
      - DATABASE_URL=postgresql://cast2md:${POSTGRES_PASSWORD}@postgres:5432/cast2md
    depends_on:
      - postgres

  postgres:
    image: pgvector/pgvector:pg16
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=cast2md
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=cast2md
```

## Management

### View Logs

```bash
docker compose logs -f cast2md
```

### Restart

```bash
docker compose restart cast2md
```

### Stop

```bash
docker compose down
```

### Update

```bash
docker compose pull
docker compose up -d
```

### Backup

```bash
# Database backup
docker compose exec cast2md cast2md backup -o /data/backup.sql

# Or via pg_dump
docker compose exec postgres pg_dump -U cast2md cast2md > backup.sql
```

### Restore

```bash
docker compose exec cast2md cast2md restore /data/backup.sql --force
```

## Data Persistence

| Path | Content | Persisted Via |
|------|---------|---------------|
| `/data` | Audio files, transcripts, temp files | Docker volume mount |
| PostgreSQL data | Database | Named volume `pgdata` |

!!! warning
    Don't delete the `pgdata` volume unless you have a backup. It contains all your podcast data.

## Optional: Tailscale Sidecar

To expose cast2md via Tailscale:

```yaml
services:
  tailscale:
    image: tailscale/tailscale
    environment:
      - TS_AUTHKEY=${TS_AUTHKEY}
      - TS_HOSTNAME=${TAILSCALE_HOSTNAME:-cast2md}
      - TS_STATE_DIR=/var/lib/tailscale
    volumes:
      - tsstate:/var/lib/tailscale
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
```

Set in `.env`:

```bash
TS_AUTHKEY=tskey-auth-xxx
TAILSCALE_HOSTNAME=cast2md
```
