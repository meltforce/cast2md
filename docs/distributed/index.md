# Distributed Transcription

cast2md supports distributed transcription, allowing multiple machines to process transcription jobs in parallel with the main server.

## Overview

The distributed system uses a **pull-based architecture** where remote "transcriber nodes" poll the server for work. This is NAT/firewall friendly and provides natural load balancing.

```
┌──────────────────────┐
│    cast2md Server    │
│  (job coordinator)   │
└──────────┬───────────┘
           │ HTTP API
    ┌──────┼──────┐
    │      │      │
    ▼      ▼      ▼
  Node   Node   RunPod
  (Mac)  (GPU)  (A5000)
```

## Node Types

| Type | Description | Setup |
|------|-------------|-------|
| **Local machine** | Mac, Linux, or Windows PC on your network | [Setup Guide](setup.md) |
| **RunPod GPU pod** | On-demand cloud GPU for batch processing | [RunPod Guide](runpod.md) |

## When to Use

| Scenario | Recommendation |
|----------|---------------|
| < 10 episodes/day | Server only, no nodes needed |
| 10-50 episodes/day | 1-2 local nodes (M4 Mac or GPU) |
| Large backlog (100+) | RunPod GPU pods |
| Ongoing high volume | Combination of local nodes + RunPod |

## Documentation

| Page | Description |
|------|-------------|
| [Setup Guide](setup.md) | Step-by-step setup for server and nodes |
| [Architecture](architecture.md) | System design, components, data flow |
| [RunPod GPU Workers](runpod.md) | On-demand GPU transcription |
| [Performance](performance.md) | GPU benchmarks and scaling recommendations |
