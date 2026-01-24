"""RunPod management API endpoints."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cast2md.services.runpod_service import (
    PodInfo,
    PodSetupPhase,
    PodSetupState,
    get_runpod_service,
)

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
            detail="RunPod not configured. Set RUNPOD_API_KEY and RUNPOD_TS_AUTH_KEY environment variables.",
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
def create_pod():
    """Create a new pod (async). Returns instance_id for tracking."""
    service = _check_available()

    try:
        instance_id = service.create_pod_async()
        return CreatePodResponse(
            instance_id=instance_id,
            message=f"Pod creation started. Track progress with GET /api/runpod/pods/{instance_id}/setup-status",
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


class GpuTypeInfo(BaseModel):
    """GPU type information."""

    id: str
    display_name: str
    memory_gb: int | None = None


class GpuTypesResponse(BaseModel):
    """Available GPU types response."""

    gpu_types: list[GpuTypeInfo]
    source: str  # "api" or "fallback"


# Default fallback GPU types (used when API is unavailable)
FALLBACK_GPU_TYPES = [
    GpuTypeInfo(id="NVIDIA GeForce RTX 4090", display_name="RTX 4090", memory_gb=24),
    GpuTypeInfo(id="NVIDIA GeForce RTX 3090", display_name="RTX 3090", memory_gb=24),
    GpuTypeInfo(id="NVIDIA RTX A4000", display_name="RTX A4000", memory_gb=16),
    GpuTypeInfo(id="NVIDIA RTX A5000", display_name="RTX A5000", memory_gb=24),
    GpuTypeInfo(id="NVIDIA GeForce RTX 4080", display_name="RTX 4080", memory_gb=16),
    GpuTypeInfo(id="NVIDIA RTX A6000", display_name="RTX A6000", memory_gb=48),
    GpuTypeInfo(id="NVIDIA L4", display_name="L4", memory_gb=24),
    GpuTypeInfo(id="NVIDIA L40", display_name="L40", memory_gb=48),
]


@router.get("/gpu-types", response_model=GpuTypesResponse)
def get_gpu_types():
    """Get available GPU types from RunPod API."""
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
