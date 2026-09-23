"""Domain data models."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class MediaItem:
    """A media file registered in the working list.

    Result fields (``transcript_*`` / ``translation_*``) are optional and
    persisted per file. They all default to None so old ``items.json`` entries
    that only carry ``path``/``alias``/``added_at`` still load via
    ``MediaItem(**entry)``.
    """

    path: str
    alias: str
    added_at: str  # ISO-8601 timestamp captured when the entry was added

    annotation: str = ""  # user note per file; old entries default to ""

    transcript_text: str | None = None
    transcript_lang: str | None = None
    transcript_segments: list | None = None
    translation_text: str | None = None
    translation_lang: str | None = None
    translation_segments: list | None = None

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

    def to_dict(self) -> dict:
        """JSON-ready dict (dataclasses.asdict) for repository persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TimedSegment":
        """Rebuild a segment persisted via :meth:`to_dict`."""
        return cls(**d)


@dataclass
class RunResult:
    """Output of a pipeline run; empty/None fields mean "not produced".

    ``transcribe`` fills the transcript side; ``transcribe_and_translate``
    fills both sides (transcript holds the ORIGINAL text and segments,
    translation the translated ones).
    """

    transcript_text: str = ""
    transcript_lang: str = ""
    transcript_segments: list[TimedSegment] | None = None
    translation_text: str = ""
    translation_lang: str = ""
    translation_segments: list[TimedSegment] | None = None