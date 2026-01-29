# Whisper Models

Guide to selecting the right transcription model for your hardware and requirements.

## Whisper Model Comparison

| Model | Quality | Speed (CPU) | RAM | Use Case |
|-------|---------|-------------|-----|----------|
| `tiny` | Basic | ~10x realtime | 1 GB | Testing only |
| `base` | Good | ~5x realtime | 2 GB | Low-resource servers |
| `small` | Very good | ~2x realtime | 3 GB | Budget hardware |
| `medium` | Excellent | ~1x realtime | 6 GB | Good balance |
| `large-v3-turbo` | Best | ~0.5x realtime (CPU) | 3 GB | **Recommended** |
| `large-v3` | Best | ~0.3x realtime (CPU) | 4 GB | Maximum quality |
| `large-v2` | Great | ~0.3x realtime (CPU) | 4 GB | Legacy |

!!! tip "Recommendation"
    Use **`large-v3-turbo`** for the best speed/quality balance. It's nearly as accurate as `large-v3` but significantly faster, with lower memory requirements.

## Parakeet Models

For RunPod GPU workers, Parakeet models offer dramatically faster transcription:

| Model | Languages | Speed (GPU) | Use Case |
|-------|-----------|-------------|----------|
| `parakeet-tdt-0.6b-v3` | 25 EU languages | ~100x realtime | **Default for RunPod** |

!!! info
    Parakeet models support 25 European languages including English, German, French, Spanish, Italian, and more. For non-European languages, use Whisper models.

## Configuration

Set the model via environment variable or web UI Settings page:

```bash
# In .env
WHISPER_MODEL=large-v3-turbo
```

## Backend Selection

| Backend | Platform | Set Via |
|---------|----------|---------|
| `faster-whisper` | CPU, NVIDIA GPU | `WHISPER_BACKEND=faster-whisper` |
| `mlx` | Apple Silicon | `WHISPER_BACKEND=mlx` |
| `auto` | Auto-detect | `WHISPER_BACKEND=auto` (default) |

With `auto`, cast2md selects `mlx` on Apple Silicon and `faster-whisper` everywhere else.

## Device and Precision

| Setting | Options | Notes |
|---------|---------|-------|
| `WHISPER_DEVICE` | `cpu`, `cuda`, `auto` | `auto` detects CUDA availability |
| `WHISPER_COMPUTE_TYPE` | `int8`, `float16`, `float32` | `int8` uses least memory |

### Recommended Configurations

=== "Apple Silicon (M1-M4)"

    ```bash
    WHISPER_BACKEND=mlx
    WHISPER_MODEL=large-v3-turbo
    ```

=== "NVIDIA GPU (8GB+ VRAM)"

    ```bash
    WHISPER_BACKEND=faster-whisper
    WHISPER_MODEL=large-v3-turbo
    WHISPER_DEVICE=cuda
    WHISPER_COMPUTE_TYPE=float16
    ```

=== "NVIDIA GPU (4-6GB VRAM)"

    ```bash
    WHISPER_BACKEND=faster-whisper
    WHISPER_MODEL=medium
    WHISPER_DEVICE=cuda
    WHISPER_COMPUTE_TYPE=int8
    ```

=== "CPU Server"

    ```bash
    WHISPER_BACKEND=faster-whisper
    WHISPER_MODEL=base
    WHISPER_DEVICE=cpu
    WHISPER_COMPUTE_TYPE=int8
    ```

## Chunked Processing

Long episodes are automatically split into chunks to prevent out-of-memory errors:

| Setting | Default | Description |
|---------|---------|-------------|
| `WHISPER_CHUNK_THRESHOLD_MINUTES` | `30` | Episodes longer than this are chunked |
| `WHISPER_CHUNK_SIZE_MINUTES` | `30` | Size of each chunk |

This enables 8GB machines to process multi-hour episodes with `large-v3-turbo`.

## Model Management (RunPod)

RunPod pods can use different models. The model is configured per-pod via the Settings page:

1. Go to Settings -> RunPod
2. Under "Manage Transcription Models", add or remove models
3. Select the default model for new pods

API: `GET/POST/DELETE /api/runpod/models`

## Re-transcription

Episodes can be re-transcribed with a newer or better model:

- **API**: `POST /api/queue/episodes/{id}/retranscribe`
- **Batch**: `POST /api/queue/batch/feed/{id}/retranscribe` (re-transcribes all episodes using an outdated model)

The `transcript_model` column on each episode tracks which model produced the current transcript.
