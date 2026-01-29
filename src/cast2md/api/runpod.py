"""RunPod management API endpoints."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from cast2md.services.runpod_service import (
    PodInfo,
    PodSetupPhase,
    PodSetupState,
    get_runpod_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runpod", tags=["runpod"])


class PodSetupStateResponse(BaseModel):
    """Pod setup state response."""

    instance_id: str
    pod_id: str | None
    pod_name: str
    ts_hostname: str
    node_name: str
    gpu_type: str
    phase: str
    message: str
    started_at: str
    error: str | None
    host_ip: str | None
    persistent: bool = False


class PodInfoResponse(BaseModel):
    """Running pod info response."""

    id: str
    name: str
    status: str
    gpu_type: str
    created_at: str | None


class RunPodStatusResponse(BaseModel):
    """RunPod status response."""

    available: bool
    enabled: bool
    can_create: bool
    can_create_reason: str
    max_pods: int
    active_pods: list[PodInfoResponse]
    setup_states: list[PodSetupStateResponse]
    auto_scale_enabled: bool
    scale_threshold: int


class CreatePodRequest(BaseModel):
    """Create pod request."""

    persistent: bool = False  # Dev mode: don't auto-terminate, allow code updates


class CreatePodResponse(BaseModel):
    """Create pod response."""

    instance_id: str
    message: str


class TerminateResponse(BaseModel):
    """Terminate pods response."""

    terminated_count: int
    message: str


def _state_to_response(state: PodSetupState) -> PodSetupStateResponse:
    """Convert PodSetupState to response model."""
    return PodSetupStateResponse(
        instance_id=state.instance_id,
        pod_id=state.pod_id,
        pod_name=state.pod_name,
        ts_hostname=state.ts_hostname,
        node_name=state.node_name,
        gpu_type=state.gpu_type,
        phase=state.phase.value,
        message=state.message,
        started_at=state.started_at.isoformat(),
        error=state.error,
        host_ip=state.host_ip,
        persistent=state.persistent,
    )


def _pod_to_response(pod: PodInfo) -> PodInfoResponse:
    """Convert PodInfo to response model."""
    return PodInfoResponse(
        id=pod.id,
        name=pod.name,
        status=pod.status,
        gpu_type=pod.gpu_type,
        created_at=pod.created_at,
    )


def _check_available():
    """Check if RunPod is available, raise 503 if not."""
    service = get_runpod_service()
    if not service.is_available():
        raise HTTPException(
            status_code=503,
            detail="RunPod not configured. Set RUNPOD_API_KEY environment variable.",
        )
    return service


@router.get("/status", response_model=RunPodStatusResponse)
def get_status():
    """Get RunPod configuration status and active pods."""
    service = get_runpod_service()

    # Get availability info
    available = service.is_available()
    enabled = service.is_enabled()

    # Check if we can create (only if available)
    can_create = False
    can_create_reason = ""
    if available:
        can_create, can_create_reason = service.can_create_pod()

    # Get active pods and setup states (only if available)
    active_pods: list[PodInfoResponse] = []
    setup_states: list[PodSetupStateResponse] = []
    if available:
        active_pods = [_pod_to_response(p) for p in service.list_pods()]
        setup_states = [_state_to_response(s) for s in service.get_setup_states()]

    return RunPodStatusResponse(
        available=available,
        enabled=enabled,
        can_create=can_create,
        can_create_reason=can_create_reason,
        max_pods=service.settings.runpod_max_pods,
        active_pods=active_pods,
        setup_states=setup_states,
        auto_scale_enabled=service.settings.runpod_auto_scale,
        scale_threshold=service.settings.runpod_scale_threshold,
    )


@router.post("/pods", response_model=CreatePodResponse)
def create_pod(request: CreatePodRequest | None = None):
    """Create a new pod (async). Returns instance_id for tracking.

    Pass {"persistent": true} to create a dev pod that won't auto-terminate.
    """
    service = _check_available()
    persistent = request.persistent if request else False

    try:
        instance_id = service.create_pod_async(persistent=persistent)
        mode = "dev mode (persistent)" if persistent else "production mode"
        return CreatePodResponse(
            instance_id=instance_id,
            message=f"Pod creation started in {mode}. Track progress with GET /api/runpod/pods/{instance_id}/setup-status",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/pods/{instance_id}/setup-status", response_model=PodSetupStateResponse)
def get_setup_status(instance_id: str):
    """Get setup progress for a pod being created."""
    service = _check_available()

    state = service.get_setup_state(instance_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"No setup state found for instance {instance_id}")

    return _state_to_response(state)


class SetupProgressRequest(BaseModel):
    """Pod self-setup progress report."""

    phase: str  # connecting, installing, ready, failed
    message: str = ""
    error: str | None = None
    host_ip: str | None = None


@router.post("/pods/{instance_id}/setup-progress", response_model=dict)
def report_setup_progress(instance_id: str, request: SetupProgressRequest, http_request: Request):
    """Report setup progress from a self-setting-up pod.

    Authenticated by X-Setup-Token header (one-time token generated at pod creation).
    """
    service = get_runpod_service()

    # Validate setup token
    token = http_request.headers.get("X-Setup-Token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-Setup-Token header")

    state = service.get_setup_state(instance_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"No setup state found for instance {instance_id}")

    if not state.setup_token or state.setup_token != token:
        raise HTTPException(status_code=403, detail="Invalid setup token")

    # Validate phase
    valid_phases = {"connecting", "installing", "ready", "failed"}
    if request.phase not in valid_phases:
        raise HTTPException(status_code=400, detail=f"Invalid phase: {request.phase}. Must be one of {valid_phases}")

    # Update state
    update_kwargs: dict = {
        "phase": PodSetupPhase(request.phase),
        "message": request.message,
    }
    if request.host_ip:
        update_kwargs["host_ip"] = request.host_ip
    if request.error:
        update_kwargs["error"] = request.error

    service._update_state(instance_id, **update_kwargs)

    # On ready: record pod run
    if request.phase == "ready" and state.pod_id:
        service._record_pod_run(
            instance_id=state.instance_id,
            pod_id=state.pod_id,
            pod_name=state.pod_name,
            gpu_type=state.gpu_type,
            started_at=state.started_at,
        )
        logger.info(f"Pod {instance_id} self-setup complete")

    if request.phase == "failed":
        logger.error(f"Pod {instance_id} self-setup failed: {request.error or request.message}")

    return {"status": "ok"}


@router.delete("/pods", response_model=TerminateResponse)
def terminate_all():
    """Terminate all running pods."""
    service = _check_available()

    count = service.terminate_all()
    return TerminateResponse(
        terminated_count=count,
        message=f"Terminated {count} pod(s)",
    )


@router.delete("/pods/{pod_id}", response_model=TerminateResponse)
def terminate_pod(pod_id: str):
    """Terminate a specific pod."""
    service = _check_available()

    success = service.terminate_pod(pod_id)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to terminate pod {pod_id}")

    return TerminateResponse(
        terminated_count=1,
        message=f"Terminated pod {pod_id}",
    )


@router.post("/pods/cleanup-states", response_model=dict)
def cleanup_states():
    """Remove stale setup states (older than 24 hours, completed or failed)."""
    service = _check_available()

    removed = service.cleanup_stale_states()
    return {"removed": removed, "message": f"Removed {removed} stale setup state(s)"}


@router.delete("/setup-states/{instance_id}", response_model=dict)
def dismiss_setup_state(instance_id: str):
    """Dismiss/clear a setup state (typically a failed one)."""
    service = _check_available()

    success = service.dismiss_setup_state(instance_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"No setup state found for instance {instance_id}")

    return {"message": f"Dismissed setup state {instance_id}"}


@router.post("/setup-states/cleanup-orphaned", response_model=dict)
def cleanup_orphaned_states():
    """Remove failed states whose pods no longer exist."""
    service = _check_available()

    removed = service.cleanup_orphaned_states()
    return {"removed": removed, "message": f"Removed {removed} orphaned setup state(s)"}


@router.post("/nodes/cleanup-orphaned", response_model=dict)
def cleanup_orphaned_nodes():
    """Remove offline RunPod Afterburner nodes that don't have matching pods."""
    service = _check_available()

    removed = service.cleanup_orphaned_nodes()
    return {"removed": removed, "message": f"Removed {removed} orphaned node(s)"}


class SetPersistentRequest(BaseModel):
    """Request to set persistent mode."""

    persistent: bool


@router.patch("/pods/{instance_id}/persistent", response_model=dict)
def set_pod_persistent(instance_id: str, request: SetPersistentRequest):
    """Set persistent (dev mode) flag for a pod.

    When persistent=True, the pod won't be auto-terminated and allows code updates.
    """
    service = _check_available()

    success = service.set_persistent(instance_id, request.persistent)
    if not success:
        raise HTTPException(status_code=404, detail=f"Pod {instance_id} not found")

    mode = "dev mode (persistent)" if request.persistent else "normal mode"
    return {"message": f"Pod {instance_id} set to {mode}"}


class GpuTypeInfo(BaseModel):
    """GPU type information."""

    id: str
    display_name: str
    memory_gb: int | None = None
    price_hr: float | None = None


class GpuTypesResponse(BaseModel):
    """Available GPU types response."""

    gpu_types: list[GpuTypeInfo]
    source: str  # "api" or "fallback"


# Default fallback GPU types (used when API is unavailable - no pricing)
FALLBACK_GPU_TYPES = [
    GpuTypeInfo(id="NVIDIA GeForce RTX 4090", display_name="RTX 4090", memory_gb=24),
    GpuTypeInfo(id="NVIDIA GeForce RTX 3090", display_name="RTX 3090", memory_gb=24),
    GpuTypeInfo(id="NVIDIA RTX A4000", display_name="RTX A4000", memory_gb=16),
    GpuTypeInfo(id="NVIDIA GeForce RTX 4080", display_name="RTX 4080", memory_gb=16),
    GpuTypeInfo(id="NVIDIA L4", display_name="L4", memory_gb=24),
]


@router.get("/gpu-types", response_model=GpuTypesResponse)
def get_gpu_types():
    """Get available GPU types (cached)."""
    service = get_runpod_service()

    if not service.is_available():
        return GpuTypesResponse(gpu_types=FALLBACK_GPU_TYPES, source="fallback")

    try:
        gpu_types = service.get_available_gpus()
        if gpu_types:
            return GpuTypesResponse(gpu_types=gpu_types, source="api")
    except Exception:
        pass

    return GpuTypesResponse(gpu_types=FALLBACK_GPU_TYPES, source="fallback")


@router.post("/gpu-types/refresh", response_model=GpuTypesResponse)
def refresh_gpu_types():
    """Refresh GPU types cache from RunPod API."""
    service = get_runpod_service()

    if not service.is_available():
        raise HTTPException(status_code=503, detail="RunPod not configured")

    try:
        gpu_types = service.refresh_gpu_cache()
        if gpu_types:
            return GpuTypesResponse(gpu_types=gpu_types, source="api")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh GPU types: {e}")

    return GpuTypesResponse(gpu_types=FALLBACK_GPU_TYPES, source="fallback")


# RunPod Transcription Models API

class RunPodModelInfo(BaseModel):
    """RunPod transcription model info."""

    id: str
    display_name: str
    backend: str
    is_enabled: bool
    sort_order: int


class RunPodModelsResponse(BaseModel):
    """List of RunPod transcription models."""

    models: list[RunPodModelInfo]


class AddRunPodModelRequest(BaseModel):
    """Request to add a RunPod transcription model."""

    id: str
    display_name: str
    backend: str = "whisper"  # 'whisper' or 'parakeet'


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


@router.get("/models", response_model=RunPodModelsResponse)
def list_runpod_models(include_disabled: bool = False):
    """List all RunPod transcription models."""
    from cast2md.db.connection import get_db
    from cast2md.db.repository import RunPodModelRepository

    with get_db() as conn:
        repo = RunPodModelRepository(conn)
        repo.seed_defaults()
        models = repo.get_all(enabled_only=not include_disabled)

    return RunPodModelsResponse(
        models=[
            RunPodModelInfo(
                id=m.id,
                display_name=m.display_name,
                backend=m.backend,
                is_enabled=m.is_enabled,
                sort_order=m.sort_order,
            )
            for m in models
        ]
    )


@router.post("/models", response_model=MessageResponse)
def add_runpod_model(request: AddRunPodModelRequest):
    """Add a custom RunPod transcription model."""
    from cast2md.db.connection import get_db
    from cast2md.db.repository import RunPodModelRepository

    with get_db() as conn:
        repo = RunPodModelRepository(conn)
        # Get max sort_order and add after
        models = repo.get_all(enabled_only=False)
        max_order = max((m.sort_order for m in models), default=0)

        repo.upsert(
            model_id=request.id,
            display_name=request.display_name,
            backend=request.backend,
            is_enabled=True,
            sort_order=max_order + 10,
        )

    return MessageResponse(message=f"Model '{request.id}' added.")


@router.delete("/models/{model_id}", response_model=MessageResponse)
def delete_runpod_model(model_id: str):
    """Delete a RunPod transcription model."""
    from cast2md.db.connection import get_db
    from cast2md.db.repository import RunPodModelRepository

    with get_db() as conn:
        repo = RunPodModelRepository(conn)
        if repo.delete(model_id):
            return MessageResponse(message=f"Model '{model_id}' deleted.")
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found.")


@router.post("/models/reset", response_model=MessageResponse)
def reset_runpod_models():
    """Reset RunPod models to defaults."""
    from cast2md.db.connection import get_db
    from cast2md.db.repository import RunPodModelRepository

    with get_db() as conn:
        # Clear all models
        cursor = conn.cursor()
        cursor.execute("DELETE FROM runpod_models")
        conn.commit()
        # Re-seed defaults
        repo = RunPodModelRepository(conn)
        count = repo.seed_defaults()

    return MessageResponse(message=f"Models reset to {count} defaults.")
