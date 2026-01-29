# Configuration

cast2md is configured through environment variables and a web UI settings page.

## Configuration Sources

Settings are loaded in this order (later sources override earlier ones):

1. **Default values** -- built into the application
2. **`.env` file** -- in the project root directory
3. **`~/.cast2md/.env`** -- user-level configuration
4. **Environment variables** -- system environment
5. **Database** -- settings changed via web UI

!!! note
    Environment variables always take precedence over database-stored settings. If a setting is set in `.env`, changing it in the web UI has no effect.

## Quick Setup

Copy the example configuration and edit it:

```bash
cp .env.example .env
```

The minimum required setting for Docker deployments:

```bash
POSTGRES_PASSWORD=your_secure_password
```

For manual installations, you also need:

```bash
DATABASE_URL=postgresql://cast2md:password@localhost:5432/cast2md
```

## Configuration Pages

| Page | Description |
|------|-------------|
| [Environment Variables](environment.md) | Complete reference for all env vars |
| [Whisper Models](whisper-models.md) | Model comparison table and selection guide |

## Web UI Settings

The Settings page (`/settings`) provides a web interface for commonly changed settings:

- **Transcription** -- Whisper model, device, compute type
- **Downloads** -- concurrent downloads, timeouts
- **Distributed Transcription** -- enable/disable, node management
- **RunPod** -- GPU worker configuration
- **Notifications** -- ntfy integration
