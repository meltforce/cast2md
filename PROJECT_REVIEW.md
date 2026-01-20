# Project Review: cast2md

## 1. Executive Summary

**Overall Status**: 🟢 **Production Ready**
The project is well-architected and functionally impressive, featuring a modern UI and a sophisticated distributed transcription system.

| Category | Status | Rating |
|----------|--------|--------|
| **Architecture** | Excellent | ⭐⭐⭐⭐⭐ |
| **User Interface** | Great | ⭐⭐⭐⭐ |
| **Documentation** | Good | ⭐⭐⭐ |
| **Code Quality** | Good | ⭐⭐⭐ |
| **Reliability** | Good | ⭐⭐⭐ |
| **Testing** | Basic | ⭐⭐ |

## 2. Codebase & Architecture Analysis

### Strengths
- **Modular Design**: The `src/cast2md` package is well-organized with clear separation of concerns (`api`, `db`, `feed`, `transcription`).
- **Distributed System**: The architecture for distributed transcription nodes (worker/coordinator pattern) is sophisticated and well-documented.
- **Modern Stack**: Effective use of FastAPI, Pydantic, SQLite (WAL mode), and HTMX/Jinja2 for a lightweight frontend.

### Weaknesses
- **Limited Test Coverage**: Basic tests exist for JobRepository state transitions, but more coverage would be beneficial.
- **Hardcoded Logic**: Some retry logic parameters are scattered or implicitly handled via "stale job reclamation" rather than explicit state transitions.

## 3. GUI Review

**URL**: `https://cast2md.leo-royal.ts.net`

### Impressions
- **Visuals**: Clean, minimalist interface (likely Pico.css). Responsive design works well on mobile widths.
- **Functionality**: Navigation is snappy. "Status" page gives excellent visibility into active workers.

### Findings
- **Failed Jobs**: The Queue page showed multiple failed jobs.
- **Bug Visible in UI**: One job displayed **"Attempts: 19 / 3"**. This confirms that the retry limit is being bypassed, leading to potential infinite resource consumption for broken jobs.

## 4. Resolved Issues

### ✅ 1. Infinite Retry Loop (The "19/3 Attempts" Bug) - FIXED
**Location**: `src/cast2md/db/repository.py`

**The Problem** (now fixed):
Jobs that hung or crashed workers would be reclaimed by `reclaim_stale_jobs()` without checking if they had exceeded `max_attempts`, leading to infinite retry loops.

**The Fix**:
- `reclaim_stale_jobs()` now checks `attempts >= max_attempts` and permanently fails exhausted jobs
- `reset_running_jobs()` (server startup) applies the same logic
- `batch_force_reset_stuck()` also respects max attempts
- All three methods now return `tuple[int, int]` (requeued, failed) for better observability

### ✅ 2. Test Coverage - ADDED
- **Coverage**: Basic JobRepository tests (22 tests)
- **Location**: `tests/test_job_repository.py`
- **Includes**: Regression test for the "19/3 attempts" bug to prevent reintroduction

## 5. Documentation Review
- **Architecture**: `docs/distributed-transcription-architecture.md` is excellent.
- **Deployment**: `README.md` provides good instructions for Docker and LXC.
- **Developer Guide**: Missing. No instructions on how to contribute or run local dev environments beyond basic `uv sync`.

## 6. Recommendations

### Future Improvements
1.  **CI/CD**: Set up a GitHub Action to run linting and tests on PRs.
2.  **Error Reporting**: Integrate Sentry or similar to capture *why* jobs are crashing, not just that they are stale.
3.  **Expand Test Coverage**: Add tests for other repositories (Feed, Episode) and API endpoints.
