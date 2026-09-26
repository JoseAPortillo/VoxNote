"""Domain data models."""

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


def normalize_marks(raw) -> list[list[float]] | None:
    """Return in/out marks as ordered, non-overlapping ``[start, end]`` pairs.

    Marks are absolute seconds on the media timeline, so an exported SRT can
    subtitle the original file. Callers hand over whatever they collected
    (slider positions, a stored list, a hand-edited ``items.json``) and this
    function is the single place that makes it usable: a pair that is not two
    numbers, or whose ``end`` is not after its ``start``, is dropped; the
    survivors are sorted by start; and pairs that touch or overlap are merged
    into one, so two marks on the same spot become one range instead of
    decoding that audio twice.

    ``None`` comes back for ``None``, for an empty input, and for input whose
    every pair was invalid: "no marks" stays a single state. That matters
    downstream, because the engines read an empty range list as "the whole
    file", so the absent case must never be stored as ``[]``.
    """
    if not raw:
        return None
    pairs: list[list[float]] = []
    for pair in raw:
        # A bare string is iterable but indexing it would silently yield digits
        # ("12" -> 1.0, 2.0), so the pair shape is checked before the values.
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        try:
            start, end = float(pair[0]), float(pair[1])
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        pairs.append([start, end])
    if not pairs:
        return None
    pairs.sort()
    merged: list[list[float]] = [pairs[0]]
    for start, end in pairs[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)  # touching or overlapping
        else:
            merged.append([start, end])
    return merged


@dataclass
class MediaItem:
    """A media file registered in the working list.

    Result fields (``transcript_*`` / ``translation_*``), edited-document
    fields and the in/out marks are optional and persisted per file. They all
    default to None so old ``items.json`` entries that only carry
    ``path``/``alias``/``added_at`` still load via ``MediaItem(**entry)``.
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

    # User-edited documents. ``None`` means no user edit exists; an empty
    # string is a deliberate edit that removes all text from that pane.
    transcript_edited_text: str | None = None
    translation_edited_text: str | None = None

    # In/out marks scoping the next run, as ``[start, end]`` second pairs on the
    # media timeline. ``None`` means no marks at all; see :func:`normalize_marks`
    # for the ordering/merging rules and for why the empty case is also ``None``.
    marks: list | None = None

    def __post_init__(self) -> None:
        # Enforced on every construction so the invariant holds for items
        # rebuilt by ``MediaItem(**entry)`` from a stored file too, not only for
        # the ones written through the repository.
        self.marks = normalize_marks(self.marks)

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