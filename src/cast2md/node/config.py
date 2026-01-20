"""Node configuration and credential management."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class NodeConfig:
    """Configuration for a transcriber node."""

    server_url: str
    node_id: str
    api_key: str
    name: str

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NodeConfig":
        """Create from dictionary."""
        return cls(
            server_url=data["server_url"],
            node_id=data["node_id"],
            api_key=data["api_key"],
            name=data["name"],
        )


def get_config_path() -> Path:
    """Get the path to the node configuration file."""
    config_dir = Path.home() / ".cast2md"
    return config_dir / "node.json"


def load_config() -> Optional[NodeConfig]:
    """Load node configuration from disk.

    Returns:
        NodeConfig if exists, None otherwise.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            data = json.load(f)
        return NodeConfig.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def save_config(config: NodeConfig) -> None:
    """Save node configuration to disk."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w") as f:
        json.dump(config.to_dict(), f, indent=2)


def delete_config() -> bool:
    """Delete node configuration file.

    Returns:
        True if deleted, False if not found.
    """
    config_path = get_config_path()
    if config_path.exists():
        config_path.unlink()
        return True
    return False
