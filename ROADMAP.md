# Roadmap

Open work, one line per item. A closed item is deleted. The rows with their
full notes as of 2026-11-13 are frozen in `archive/ROADMAP-2026-11-13.md`.

## CI and verification

- Give vimmary the same deploy-freshness heartbeat
- The afterburner image no longer builds

## Code structure

- The transcript-fetch card never reports an orphaned job
- Split `EpisodeRepository` and `JobRepository` by concern
- Decide whether the remaining `web/views.py` routes are worth splitting
- Add tests for `web/status_view.py`

## API

- Exercise the endpoints that had never returned a response

## Feeds and transcripts

- A poll never updates an episode that already exists

## Documentation

- Add the screenshot the README reserves a placeholder for
