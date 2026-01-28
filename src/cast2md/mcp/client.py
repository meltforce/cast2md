"""HTTP API client for MCP remote mode.

When MCP_API_URL is set, MCP tools use this client to call the remote API
instead of accessing the local database directly.
"""

import os
from typing import Any

import httpx

# Get API URL from environment
API_URL = os.environ.get("MCP_API_URL", "").rstrip("/")


def is_remote_mode() -> bool:
    """Check if MCP is configured to use remote API."""
    return bool(API_URL)


def get_client() -> httpx.Client:
    """Get HTTP client for API calls."""
    return httpx.Client(base_url=API_URL, timeout=30.0)


def search_transcripts(query: str, feed_id: int | None = None, limit: int = 20) -> dict:
    """Search transcripts via API."""
    with get_client() as client:
        params = {"q": query, "limit": limit}
        if feed_id:
            params["feed_id"] = feed_id
        resp = client.get("/api/search/transcripts", params=params)
        resp.raise_for_status()
        data = resp.json()
        return {
            "query": data["query"],
            "total": data["total"],
            "hint": "Use cast2md://episodes/{episode_id}/transcript to read full transcript",
            "results": [
                {
                    "episode_id": r["episode_id"],
                    "episode_title": r["episode_title"],
                    "feed_id": r["feed_id"],
                    "feed_title": r["feed_title"],
                    "published_at": r["published_at"],
                    "segment_start": r["segment_start"],
                    "segment_end": r["segment_end"],
                    "snippet": r["snippet"],
                }
                for r in data["results"]
            ],
        }


def semantic_search(
    query: str, feed_id: int | None = None, limit: int = 20, mode: str = "hybrid"
) -> dict:
    """Semantic search via API."""
    with get_client() as client:
        params = {"q": query, "limit": limit, "mode": mode}
        if feed_id:
            params["feed_id"] = feed_id
        resp = client.get("/api/search/semantic", params=params)
        resp.raise_for_status()
        data = resp.json()
        return {
            "query": data["query"],
            "total": data["total"],
            "mode": data["mode"],
            "hint": "Use cast2md://episodes/{episode_id}/transcript to read full transcript",
            "results": [
                {
                    "episode_id": r["episode_id"],
                    "episode_title": r["episode_title"],
                    "feed_id": r["feed_id"],
                    "feed_title": r["feed_title"],
                    "published_at": r["published_at"],
                    "segment_start": r["segment_start"],
                    "segment_end": r["segment_end"],
                    "text": r["text"],
                    "score": r["score"],
                    "match_type": r["match_type"],
                }
                for r in data["results"]
            ],
        }


def search_episodes(query: str, feed_id: int | None = None, limit: int = 25) -> dict:
    """Search episodes via API."""
    with get_client() as client:
        params = {"q": query, "limit": limit}
        if feed_id:
            params["feed_id"] = feed_id
        resp = client.get("/api/search/episodes", params=params)
        resp.raise_for_status()
        data = resp.json()
        return {
            "query": data["query"],
            "total": data["total"],
            "hint": "Use queue_episode(id) to transcribe, or cast2md://episodes/{id}/transcript to read existing transcript",
            "results": [
                {
                    "id": r["id"],
                    "feed_id": r["feed_id"],
                    "title": r["title"],
                    "description": r["description"][:500] if r.get("description") else None,
                    "published_at": r["published_at"],
                    "status": r["status"],
                    "has_transcript": r["status"] == "completed",
                }
                for r in data["results"]
            ],
        }


def queue_episode(episode_id: int) -> dict:
    """Queue episode for transcription via API."""
    with get_client() as client:
        # Try transcribe first (for downloaded episodes) - use queue API
        resp = client.post(f"/api/queue/episodes/{episode_id}/transcribe")
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "message": data.get("message", f"Queued transcription job for episode {episode_id}"),
                "job_id": data.get("job_id"),
                "job_type": "transcribe",
            }

        # Try download if transcribe failed (episode not downloaded yet)
        if resp.status_code == 400:  # Not downloaded yet
            resp = client.post(f"/api/queue/episodes/{episode_id}/download")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "message": data.get("message", f"Queued download job for episode {episode_id}"),
                    "job_id": data.get("job_id"),
                    "job_type": "download",
                }

        return {"success": False, "error": resp.text}


def get_queue_status() -> dict:
    """Get queue status via API."""
    with get_client() as client:
        resp = client.get("/api/queue/status")
        resp.raise_for_status()
        return resp.json()


def get_recent_episodes(days: int = 7, limit: int = 50) -> dict:
    """Get recent episodes via API."""
    with get_client() as client:
        resp = client.get("/api/episodes/recent", params={"days": days, "limit": limit})
        resp.raise_for_status()
        data = resp.json()
        return {
            "days": data["days"],
            "total": data["total"],
            "hint": "Use queue_episode(id) to transcribe, or cast2md://episodes/{id}/transcript to read existing transcript",
            "results": [
                {
                    "id": ep["id"],
                    "feed_id": ep["feed_id"],
                    "feed_title": ep["feed_title"],
                    "title": ep["title"],
                    "description": ep.get("description"),
                    "published_at": ep.get("published_at"),
                    "status": ep["status"],
                    "has_transcript": ep.get("has_transcript", False),
                }
                for ep in data["episodes"]
            ],
        }


def add_feed(url: str) -> dict:
    """Add feed via API."""
    with get_client() as client:
        resp = client.post("/api/feeds", json={"url": url})
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "message": f"Added feed '{data.get('title', 'Unknown')}'",
                "feed_id": data["id"],
                "title": data.get("title"),
            }
        return {"success": False, "error": resp.text}


def refresh_feed(feed_id: int, auto_queue: bool = False) -> dict:
    """Refresh feed via API."""
    with get_client() as client:
        resp = client.post(f"/api/feeds/{feed_id}/refresh", params={"auto_queue": auto_queue})
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "message": f"Refreshed feed, found {data.get('new_episodes', 0)} new episodes",
                "new_episode_count": data.get("new_episodes", 0),
            }
        return {"success": False, "error": resp.text}


def get_feeds() -> list[dict[str, Any]]:
    """Get all feeds via API."""
    with get_client() as client:
        resp = client.get("/api/feeds")
        resp.raise_for_status()
        data = resp.json()
        return data.get("feeds", data) if isinstance(data, dict) else data


def get_feed(feed_id: int) -> dict | None:
    """Get feed details via API."""
    with get_client() as client:
        resp = client.get(f"/api/feeds/{feed_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


def get_episode(episode_id: int) -> dict | None:
    """Get episode details via API."""
    with get_client() as client:
        resp = client.get(f"/api/episodes/{episode_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


def get_transcript(episode_id: int) -> str | None:
    """Get episode transcript via API."""
    with get_client() as client:
        resp = client.get(f"/api/episodes/{episode_id}/transcript", params={"format": "md"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text


def get_transcript_section(
    episode_id: int, start_time: float | None = None, duration: float = 300
) -> dict:
    """Get transcript section via API."""
    with get_client() as client:
        params: dict[str, Any] = {"duration": duration}
        if start_time is not None:
            params["start_time"] = start_time
        resp = client.get(f"/api/episodes/{episode_id}/transcript/section", params=params)
        if resp.status_code == 404:
            return {"error": f"Episode {episode_id} not found or has no transcript"}
        resp.raise_for_status()
        return resp.json()


def get_status() -> dict:
    """Get system status via API."""
    with get_client() as client:
        resp = client.get("/api/health")
        resp.raise_for_status()
        return resp.json()
