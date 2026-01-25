# Server Sizing Guide

Resource requirements for running cast2md server and transcription workers.

## Quick Reference

| Deployment | RAM | Disk | CPU | Notes |
|------------|-----|------|-----|-------|
| Server only (external transcripts) | 2 GB | 10 GB | 2 cores | No local transcription |
| Server + local Whisper (base) | 4 GB | 20 GB | 4 cores | Good for small libraries |
| Server + local Whisper (large-v3-turbo) | 4 GB | 30 GB | 4 cores | Recommended |
| Server + RunPod workers | 4 GB | 20 GB | 2 cores | Offload transcription to GPU |

## Memory Usage

### Server Process

Base memory usage:
- FastAPI + workers: ~200 MB
- PostgreSQL (Docker): ~150 MB
- Embedding model (if enabled): ~500 MB

### Transcription Memory

**With chunked processing (default for all backends):**
- Episodes are split into 30-minute chunks
- Peak memory: ~2-3 GB regardless of episode length
- Works with faster-whisper, mlx-whisper, and Parakeet
- Configured via `whisper_chunk_threshold_minutes` and `whisper_chunk_size_minutes`
- Enables 8GB M1 Macs to run large-v3-turbo on 3+ hour episodes

**Without chunking (episodes < 30 min):**
- Memory scales with audio duration
- ~1 GB for 30-minute episode
- ~2 GB for 1-hour episode

### Model Memory

| Model | VRAM/RAM | Quality | Speed |
|-------|----------|---------|-------|
| base | ~500 MB | Basic | Fast |
| small | ~1 GB | Good | Medium |
| medium | ~2 GB | Better | Slower |
| large-v3-turbo | ~3 GB | Best | Medium |
| large-v3 | ~4 GB | Best | Slow |

## Disk Usage

### Storage Breakdown

| Component | Typical Size | Notes |
|-----------|--------------|-------|
| Database | 1-5 GB | Grows with transcript count |
| Transcripts | ~50 KB/episode | Markdown with timestamps |
| Audio (if retained) | 50-200 MB/episode | Usually deleted after transcription |
| Whisper models | 0.5-3 GB | Downloaded on first use |
| Embedding model | ~500 MB | If semantic search enabled |

### Temp File Management

Temporary files are created during transcription:
- `preprocess_*.wav` - Converted audio (mono 16kHz)
- `chunk_*.wav` - Audio chunks for long episodes
- `.downloading_*` - Incomplete downloads

**Automatic cleanup:**
- Server cleans files >24 hours old on startup
- Node workers also clean on startup
- Files cleaned after each successful transcription

**Manual cleanup:**
```bash
# Check temp directory size
du -sh /opt/cast2md/data/temp

# Remove orphaned temp files
find /opt/cast2md/data/temp -name "preprocess_*.wav" -mmin +60 -delete
find /opt/cast2md/data/temp -name "chunk_*.wav" -mmin +60 -delete
```

## LXC/Container Configuration

### Recommended Settings

```
# Proxmox LXC config
arch: amd64
cores: 4
memory: 4096
swap: 2048
rootfs: local-lvm:vm-xxx-disk-0,size=26G
```

### Resource Limits

- **Memory**: 4 GB minimum with local transcription
- **Swap**: 2 GB recommended (handles occasional spikes)
- **Disk**: 26 GB comfortable for ~500 episodes with audio deleted

## Scaling Considerations

### When to Add Remote Workers

Consider RunPod or distributed nodes when:
- Queue consistently has >10 pending jobs
- Local transcription takes >2 hours to clear queue
- Processing backlog of existing podcast

### RunPod GPU Sizing

| GPU | Cost/hr | Speed | Use Case |
|-----|---------|-------|----------|
| RTX A4000 | ~$0.15 | 50x realtime | Budget option |
| RTX A5000 | ~$0.20 | 87x realtime | Recommended |
| RTX A6000 | ~$0.35 | 100x realtime | Large backlogs |

Note: RTX 40-series GPUs have CUDA compatibility issues with Parakeet.

## Monitoring

### Key Metrics

```bash
# Memory usage
free -h

# Disk usage
df -h /opt/cast2md
du -sh /opt/cast2md/data/*

# Active transcription processes
ps aux | grep -E 'whisper|ffmpeg'

# Check for orphaned temp files
ls -la /opt/cast2md/data/temp/
```

### Health Check

```bash
curl http://localhost:8000/api/health
```

## Troubleshooting

### Out of Memory

Symptoms: Process killed, incomplete transcriptions

Solutions:
1. Reduce `whisper_chunk_size_minutes` (default: 30)
2. Use smaller Whisper model
3. Add swap space
4. Offload to RunPod workers

### Disk Full

Symptoms: Failed downloads, database errors

Solutions:
1. Check for orphaned temp files
2. Delete processed audio files
3. Clean old trash: `cleanup_old_trash(days=7)`
4. Increase disk allocation

### Slow Transcription

Symptoms: Jobs taking hours instead of minutes

Solutions:
1. Enable GPU acceleration (CUDA)
2. Use `large-v3-turbo` instead of `large-v3`
3. Add distributed worker nodes
4. Use RunPod for batch processing
