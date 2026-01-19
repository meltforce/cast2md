"""ntfy notification service."""

import logging
from enum import Enum

import httpx

from cast2md.config.settings import get_settings

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Types of notifications."""

    TRANSCRIPTION_COMPLETE = "transcription_complete"
    TRANSCRIPTION_FAILED = "transcription_failed"
    DOWNLOAD_COMPLETE = "download_complete"
    DOWNLOAD_FAILED = "download_failed"


def send_notification(
    notification_type: NotificationType,
    title: str,
    message: str,
    priority: int = 3,
    tags: list[str] | None = None,
) -> bool:
    """Send a notification via ntfy.

    Args:
        notification_type: Type of notification for categorization.
        title: Notification title.
        message: Notification body.
        priority: 1 (min) to 5 (max), default 3.
        tags: Optional list of emoji tags.

    Returns:
        True if sent successfully, False otherwise.
    """
    settings = get_settings()

    if not settings.ntfy_enabled:
        return False

    if not settings.ntfy_topic:
        logger.warning("ntfy enabled but no topic configured")
        return False

    url = f"{settings.ntfy_url.rstrip('/')}/{settings.ntfy_topic}"

    # Default tags based on notification type
    if tags is None:
        tags = _get_default_tags(notification_type)

    headers = {
        "Title": title,
        "Priority": str(priority),
        "Tags": ",".join(tags) if tags else "",
    }

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(url, content=message, headers=headers)
            response.raise_for_status()
            logger.info(f"Notification sent: {title}")
            return True
    except httpx.HTTPError as e:
        logger.error(f"Failed to send notification: {e}")
        return False


def _get_default_tags(notification_type: NotificationType) -> list[str]:
    """Get default emoji tags for notification type."""
    tag_map = {
        NotificationType.TRANSCRIPTION_COMPLETE: ["white_check_mark", "microphone"],
        NotificationType.TRANSCRIPTION_FAILED: ["x", "microphone"],
        NotificationType.DOWNLOAD_COMPLETE: ["white_check_mark", "arrow_down"],
        NotificationType.DOWNLOAD_FAILED: ["x", "arrow_down"],
    }
    return tag_map.get(notification_type, [])


def notify_transcription_complete(episode_title: str, podcast_title: str) -> bool:
    """Send notification for completed transcription."""
    return send_notification(
        NotificationType.TRANSCRIPTION_COMPLETE,
        title=f"Transcription Complete",
        message=f"{episode_title}\n{podcast_title}",
        priority=3,
    )


def notify_transcription_failed(
    episode_title: str, podcast_title: str, error: str
) -> bool:
    """Send notification for failed transcription."""
    return send_notification(
        NotificationType.TRANSCRIPTION_FAILED,
        title=f"Transcription Failed",
        message=f"{episode_title}\n{podcast_title}\n\nError: {error}",
        priority=4,
    )


def notify_download_complete(episode_title: str, podcast_title: str) -> bool:
    """Send notification for completed download."""
    return send_notification(
        NotificationType.DOWNLOAD_COMPLETE,
        title=f"Download Complete",
        message=f"{episode_title}\n{podcast_title}",
        priority=2,
    )


def notify_download_failed(
    episode_title: str, podcast_title: str, error: str
) -> bool:
    """Send notification for failed download."""
    return send_notification(
        NotificationType.DOWNLOAD_FAILED,
        title=f"Download Failed",
        message=f"{episode_title}\n{podcast_title}\n\nError: {error}",
        priority=4,
    )
