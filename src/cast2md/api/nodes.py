"""Node API endpoints for distributed transcription."""

import secrets
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import JobStatus, JobType, NodeStatus
from cast2md.db.repository import (
    EpisodeRepository,
    JobRepository,
    TranscriberNodeRepository,
)

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def verify_node_api_key(x_transcriber_key: str = Header(None)) -> str:
    """Verify the node API key from header."""
    if not x_transcriber_key:
        raise HTTPException(status_code=401, detail="Missing X-Transcriber-Key header")
    return x_transcriber_key


def get_node_from_key(api_key: str):
    """Get the node associated with an API key."""
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        node = repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return node


# === Request/Response Models ===


class RegisterNodeRequest(BaseModel):
    """Request to register a new node."""

    name: str
    url: str
    whisper_model: str | None = None
    whisper_backend: str | None = None


class RegisterNodeResponse(BaseModel):
    """Response with node registration details."""

    node_id: str
    api_key: str
    message: str


class NodeResponse(BaseModel):
    """Response with node details."""

    id: str
    name: str
    url: str
    whisper_model: str | None
    whisper_backend: str | None
    status: str
    last_heartbeat: str | None
    current_job_id: int | None
    priority: int


class NodesListResponse(BaseModel):
    """Response with list of nodes."""

    nodes: list[NodeResponse]
    total: int


class HeartbeatRequest(BaseModel):
    """Heartbeat request from node."""

    whisper_model: str | None = None
    whisper_backend: str | None = None


class HeartbeatResponse(BaseModel):
    """Heartbeat response."""

    status: str
    message: str


class ClaimJobResponse(BaseModel):
    """Response when claiming a job."""

    job_id: int | None
    episode_id: int | None
    episode_title: str | None
    audio_url: str
    has_job: bool


class JobCompleteRequest(BaseModel):
    """Request to mark a job as complete."""

    transcript_text: str


class JobFailRequest(BaseModel):
    """Request to mark a job as failed."""

    error_message: str


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class AddNodeRequest(BaseModel):
    """Admin request to add a node."""

    name: str
    url: str
    whisper_model: str | None = None
    whisper_backend: str | None = None
    priority: int = 10


# === Node Registration Endpoints (called by nodes) ===


@router.post("/register", response_model=RegisterNodeResponse)
def register_node(request: RegisterNodeRequest):
    """Register a new transcriber node.

    This endpoint is called by nodes during initial setup to get credentials.
    """
    node_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)

    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        repo.create(
            node_id=node_id,
            name=request.name,
            url=request.url,
            api_key=api_key,
            whisper_model=request.whisper_model,
            whisper_backend=request.whisper_backend,
        )

    return RegisterNodeResponse(
        node_id=node_id,
        api_key=api_key,
        message=f"Node '{request.name}' registered successfully",
    )


@router.post("/{node_id}/heartbeat", response_model=HeartbeatResponse)
def node_heartbeat(
    node_id: str,
    request: HeartbeatRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Receive heartbeat from a node.

    Nodes should call this every 30 seconds to indicate they're alive.
    """
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        node = repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

        if node.api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for this node")

        # Update heartbeat and optionally node info
        repo.update_heartbeat(node_id)

        if request.whisper_model or request.whisper_backend:
            repo.update_info(
                node_id,
                whisper_model=request.whisper_model or node.whisper_model,
                whisper_backend=request.whisper_backend or node.whisper_backend,
            )

        # If node was offline, mark it as online
        if node.status == NodeStatus.OFFLINE:
            repo.update_status(node_id, NodeStatus.ONLINE)

    return HeartbeatResponse(status="ok", message="Heartbeat received")


@router.post("/{node_id}/claim", response_model=ClaimJobResponse)
def claim_job(
    node_id: str,
    api_key: str = Depends(verify_node_api_key),
):
    """Claim the next available transcription job.

    The node will poll this endpoint to get work. If a job is available,
    it will be assigned to the node.
    """
    with get_db() as conn:
        node_repo = TranscriberNodeRepository(conn)
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_id(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

        if node.api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for this node")

        # Update heartbeat
        node_repo.update_heartbeat(node_id)

        # Get next unclaimed transcription job
        job = job_repo.get_next_unclaimed_job(JobType.TRANSCRIBE)

        if not job:
            return ClaimJobResponse(
                job_id=None,
                episode_id=None,
                episode_title=None,
                audio_url="",
                has_job=False,
            )

        # Claim the job
        job_repo.claim_job(job.id, node_id)

        # Update node status to busy
        node_repo.update_status(node_id, NodeStatus.BUSY, current_job_id=job.id)

        # Get episode details
        episode = episode_repo.get_by_id(job.episode_id)

        return ClaimJobResponse(
            job_id=job.id,
            episode_id=job.episode_id,
            episode_title=episode.title if episode else None,
            audio_url=f"/api/nodes/jobs/{job.id}/audio",
            has_job=True,
        )


@router.get("/jobs/{job_id}/audio")
def get_job_audio(
    job_id: int,
    api_key: str = Depends(verify_node_api_key),
):
    """Stream the audio file for a job to the node."""
    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        # Verify API key belongs to a valid node
        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Verify this node owns this job
        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        episode = episode_repo.get_by_id(job.episode_id)
        if not episode or not episode.audio_path:
            raise HTTPException(status_code=404, detail="Audio file not found")

        audio_path = Path(episode.audio_path)
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")

        return FileResponse(
            path=audio_path,
            media_type="audio/mpeg",
            filename=audio_path.name,
        )


@router.post("/jobs/{job_id}/complete", response_model=MessageResponse)
def complete_job(
    job_id: int,
    request: JobCompleteRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Mark a job as complete and submit the transcript.

    The node calls this after successfully transcribing the audio.
    """
    from cast2md.search.repository import TranscriptSearchRepository
    from cast2md.storage.filesystem import get_storage

    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        episode = episode_repo.get_by_id(job.episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Save the transcript
        from cast2md.db.repository import FeedRepository

        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(episode.feed_id)

        storage = get_storage()
        transcript_path = storage.save_transcript(
            feed_title=feed.display_title if feed else "unknown",
            episode_title=episode.title,
            content=request.transcript_text,
        )

        # Update episode
        from cast2md.db.models import EpisodeStatus

        episode_repo.update_transcript_path(episode.id, str(transcript_path))
        episode_repo.update_status(episode.id, EpisodeStatus.COMPLETED)

        # Index transcript for search
        search_repo = TranscriptSearchRepository(conn)
        search_repo.index_episode(episode.id, str(transcript_path))

        # Mark job complete
        job_repo.mark_completed(job_id)

        # Update node status back to online
        node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message="Job completed successfully")


@router.post("/jobs/{job_id}/fail", response_model=MessageResponse)
def fail_job(
    job_id: int,
    request: JobFailRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Mark a job as failed.

    The node calls this if transcription fails.
    """
    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        # Update episode status
        from cast2md.db.models import EpisodeStatus

        episode_repo.update_status(job.episode_id, EpisodeStatus.FAILED, request.error_message)

        # Mark job failed - this will handle retry logic
        job_repo.mark_failed(job_id, request.error_message)

        # Also unclaim the job so it can be picked up again
        job_repo.unclaim_job(job_id)

        # Update node status back to online
        node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message="Job marked as failed")


# === Admin Endpoints (for UI/management) ===


@router.get("", response_model=NodesListResponse)
def list_nodes():
    """List all registered nodes (admin endpoint)."""
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        nodes = repo.get_all()

    return NodesListResponse(
        nodes=[
            NodeResponse(
                id=n.id,
                name=n.name,
                url=n.url,
                whisper_model=n.whisper_model,
                whisper_backend=n.whisper_backend,
                status=n.status.value,
                last_heartbeat=n.last_heartbeat.isoformat() if n.last_heartbeat else None,
                current_job_id=n.current_job_id,
                priority=n.priority,
            )
            for n in nodes
        ],
        total=len(nodes),
    )


@router.post("", response_model=RegisterNodeResponse)
def admin_add_node(request: AddNodeRequest):
    """Manually add a node (admin endpoint)."""
    node_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)

    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        repo.create(
            node_id=node_id,
            name=request.name,
            url=request.url,
            api_key=api_key,
            whisper_model=request.whisper_model,
            whisper_backend=request.whisper_backend,
            priority=request.priority,
        )

    return RegisterNodeResponse(
        node_id=node_id,
        api_key=api_key,
        message=f"Node '{request.name}' added successfully",
    )


@router.delete("/{node_id}", response_model=MessageResponse)
def delete_node(node_id: str):
    """Delete a node (admin endpoint)."""
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        if repo.delete(node_id):
            return MessageResponse(message="Node deleted")
        raise HTTPException(status_code=404, detail="Node not found")


@router.post("/{node_id}/test", response_model=MessageResponse)
def test_node(node_id: str):
    """Test connectivity to a node (admin endpoint)."""
    import httpx

    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        node = repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

    try:
        # Try to reach the node's status endpoint
        response = httpx.get(f"{node.url}/status", timeout=5.0)
        if response.status_code == 200:
            return MessageResponse(message=f"Node '{node.name}' is reachable")
        else:
            return MessageResponse(message=f"Node returned status {response.status_code}")
    except httpx.RequestError as e:
        return MessageResponse(message=f"Failed to reach node: {e}")
