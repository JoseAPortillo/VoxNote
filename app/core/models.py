"""Domain data models."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class MediaItem:
    """A media file registered in the working list."""

    path: str
    alias: str
    added_at: str  # ISO-8601 timestamp captured when the entry was added

    @classmethod
    def from_path(cls, path: str) -> "MediaItem":
        file_path = Path(path)
        return cls(
            path=str(file_path),
            alias=file_path.stem,
            added_at=datetime.now().isoformat(timespec="seconds"),
        )


@dataclass
class TimedSegment:
    """A transcript segment with its timing and optional speaker label."""

    start: float  # seconds
    end: float    # seconds
    text: str
    speaker: str | None  # friendly 1-based speaker id, or None when unknown