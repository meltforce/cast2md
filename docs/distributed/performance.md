# RunPod GPU Performance

Performance benchmarks and optimization guide for RunPod GPU transcription workers.

## GPU Comparison

### Parakeet-Compatible GPUs

| GPU | Price/hr | Speed | $/episode-hr | Episodes/$ |
|-----|----------|-------|--------------|------------|
| RTX A4000 | $0.16 | ~50x realtime | $0.0032 | 312 |
| **RTX A5000** | **$0.22** | **~87x realtime** | **$0.0025** | **395** |
| RTX 3090 | $0.30 | ~80x realtime | $0.0038 | 267 |
| RTX A6000 | $0.45 | ~110x realtime | $0.0041 | 244 |

!!! tip "Best Value"
    **RTX A5000** offers the best cost per episode despite not being the fastest or cheapest GPU.

### Blocked GPUs (CUDA Error 35)

These GPUs fail with NeMo/Parakeet due to CUDA compatibility issues (Ada Lovelace architecture):

- NVIDIA GeForce RTX 4090
- NVIDIA GeForce RTX 4080
- NVIDIA L4

Ampere GPUs (A-series, RTX 30-series) work fine.

---

## Bandwidth Analysis

### Upload Capacity vs GPU Speed

At typical podcast bitrate (~128 kbps = 0.96 MB/min of audio):

| Bandwidth | MB/s | Realtime Equivalent |
|-----------|------|---------------------|
| 25 Mbit/s | 3.1 | ~195x |
| 50 Mbit/s | 6.25 | ~390x |
| 100 Mbit/s | 12.5 | ~780x |

!!! info "Key Finding"
    Even the fastest GPU (~110x) only needs **~1 Mbit/s** to stay saturated. At 50 Mbit/s upload, you can feed ~4-5 pods before bandwidth becomes a bottleneck.

---

## Multi-Pod Scaling

### Test Results (2x A5000 + MacBook)

**Configuration:**

- 2x RunPod A5000 pods (Parakeet)
- 1x MacBook local worker (Whisper large-v3-turbo)
- 50 Mbit/s upload bandwidth

**Observed Performance:**

- Last hour: 79 episodes, 8271 audio minutes
- Throughput: **138 hours of audio per wall-clock hour**
- Both pods stayed constantly busy (no idle time)

### Per-Node Stats (24h sample)

| Node | Jobs | Avg Time | Notes |
|------|------|----------|-------|
| RunPod A5000 (1) | 78 | 320 sec | Parakeet |
| RunPod A5000 (2) | 41 | 343 sec | Parakeet (newer pod) |
| MacBook | 326 | 344 sec | Whisper large-v3-turbo |

---

## Scaling Recommendations

### When to Add Pods

| Queue Size | Pods | Reasoning |
|------------|------|-----------|
| < 50 | 1 | Single pod sufficient |
| 50-200 | 2 | Parallel processing, no bandwidth issues |
| 200-500 | 3 | Still within 50 Mbit/s capacity |
| > 500 | 3-4 | Consider auto-scale |

### Bottlenecks (in order)

1. **GPU processing** -- primary bottleneck, scales with pods
2. **Episode length** -- longer episodes = lower throughput
3. **Job coordination** -- minor overhead between jobs
4. **Bandwidth** -- only limiting at 5+ pods

### Verification

To confirm pods aren't bandwidth-limited:

```bash
# Check if pods are busy
curl -s https://server/api/nodes | jq '.nodes[] | {name, status}'

# Check running jobs (should be 2-3x pod count for prefetch)
curl -s https://server/api/queue/status | jq '.transcribe_queue.running'
```

---

## Cost Optimization

### A5000 Economics

| Metric | Value |
|--------|-------|
| Hourly cost | ~$0.22 |
| Processing speed | 87x realtime |
| Cost per episode-hour | $0.0025 |
| **100 episodes (avg 2 hrs each)** | **~$0.50** |

### Comparison to Local Processing

| Method | Speed | Cost/100 episodes |
|--------|-------|-------------------|
| RunPod A5000 | 87x | $0.50 |
| MacBook M1 (MLX) | ~15x | Free (electricity) |
| Server CPU | ~1x | Free (electricity) |

---

## Cost Control Tips

1. **Start pods only when queue has work** -- empty queue = wasted billing
2. **Use auto-scale wisely** -- only enable for regular large backlogs
3. **Monitor stuck jobs** -- 10-min idle timeout catches these
4. **Server reliability** -- 5-min unreachable timeout prevents orphaned pods
