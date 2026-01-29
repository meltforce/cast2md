# Deployment

This section covers deploying cast2md to production.

## Recommended Setup

cast2md runs as a Docker Compose stack with two containers:

- **cast2md** -- the application (FastAPI + workers)
- **PostgreSQL** -- database with pgvector extension

### Docker Compose Deployment

```bash
# On the production server
mkdir -p /opt/cast2md
cd /opt/cast2md

# Create docker-compose.yml and .env
# (copy from repo or use compose.example.yml as reference)

# Start the stack
docker compose up -d
```

### Building and Deploying

```bash
# Build on dev machine
docker build -t meltforce/cast2md:2026.01 --build-arg VERSION=2026.01 .

# Transfer to production server
docker save meltforce/cast2md:2026.01 | ssh root@server "docker load"

# Restart on production
ssh root@server "cd /opt/cast2md && docker compose up -d cast2md"
```

!!! warning
    Always test on a development machine first. Repeated restarts on production disrupt workers, nodes, and job state.

### Environment Configuration

The production `.env` file contains:

```bash
# Required
POSTGRES_PASSWORD=your_secure_password

# Optional
PORT=8000
DATA_PATH=./data
WHISPER_MODEL=large-v3-turbo

# RunPod (if using GPU workers)
RUNPOD_ENABLED=true
RUNPOD_API_KEY=...
RUNPOD_TS_AUTH_KEY=...
RUNPOD_SERVER_URL=https://your-server.ts.net
RUNPOD_SERVER_IP=100.x.x.x
```

!!! danger
    The `.env` file contains secrets (API keys, database credentials). Never commit it to git.

## Management

### Viewing Logs

```bash
ssh root@server "cd /opt/cast2md && docker compose logs -f cast2md"
```

### Checking Status

```bash
# Container status
ssh root@server "docker compose -f /opt/cast2md/docker-compose.yml ps"

# Application health
curl https://your-server/api/health
```

### Backup

```bash
# From inside the container
docker compose exec cast2md cast2md backup -o /data/backup.sql

# Or direct pg_dump
docker compose exec postgres pg_dump -U cast2md cast2md > backup.sql
```

## Further Reading

- [Server Sizing](server-sizing.md) -- resource requirements and scaling
- [Configuration](../configuration/index.md) -- all available settings
- [Distributed Transcription](../distributed/index.md) -- remote worker setup
