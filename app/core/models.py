"""Domain data models."""

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


def normalize_slice(raw) -> list[float] | None:
    """Return one ``[start, end]`` pair in seconds, or ``None`` for no slice.

    The slice is absolute seconds on the media timeline, so an exported SRT can
    still subtitle the original file. Callers hand over whatever they have (two
    slider positions, a stored pair, a hand-edited ``items.json``) and this
    function is the single place that makes it usable.

    ``None`` is the ONLY representation of "no slice", never ``[]``. That is
    not a style choice: the transcription engines read an empty range list as
    "the whole file", so an empty list stored as "no slice" would be
    indistinguishable from no slice when reading the item and would mean the
    exact opposite when handed to the engine -- the run would transcribe the
    whole medium with nothing in the UI reporting it. Every unusable input
    (wrong length, non-numeric, NaN/inf, negative, zero or inverted range)
    therefore normalizes to ``None``: the same state as never having moved a
    handle.

    The pair comes back as a list of float, not a tuple. JSON has no tuple, so
    a tuple written to ``items.json`` returns from ``json.loads`` as a list and
    ``item.slice == [1.0, 5.0]`` would then be False for the stored value;
    storing lists keeps one save/load cycle equal by value instead of by
    accident of type.
    """
    if not isinstance(raw, (list, tuple)):
        # The container shape is checked before the values, because two
        # iterables that survive this check silently become plausible-looking
        # slices: a bare string indexes as characters ("12" -> 1.0, 2.0) and a
        # dict has length 2 and indexes by key.
        return None
    if len(raw) != 2:
        return None
    seconds: list[float] = []
    for value in raw:
        # A JSON boolean is an int in Python and float(False) is 0.0, so
        # [false, true] would quietly become a 0-1 s slice nobody ever set.
        if isinstance(value, bool):
            return None
        try:
            seconds.append(float(value))
        except (TypeError, ValueError):
            return None
    start, end = seconds
    if not (math.isfinite(start) and math.isfinite(end)):
        # A NaN end survives every ordering comparison below, so without this
        # it would reach the engines as a range they cannot seek or decode.
        return None
    if start < 0.0 or end <= start:
        return None
    return [start, end]


@dataclass
class MediaItem:
    """A media file registered in the working list.

    Result fields (``transcript_*`` / ``translation_*``), edited-document
    fields and the playback/execution slice are optional and persisted per
    file. They all default to None so old ``items.json`` entries that only
    carry ``path``/``alias``/``added_at`` still load via ``MediaItem(**entry)``.
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

    # The single ``[start, end]`` pair, in seconds on the media timeline, that
    # bounds playback AND scopes the next transcription/translation. It
    # persists: no run ever consumes it. ``None`` means the whole file, and is
    # the only way to say that; see :func:`normalize_slice` for why the empty
    # case is also ``None`` instead of ``[]``.
    slice: list | None = None

    def __post_init__(self) -> None:
        # Enforced on every construction so the invariant holds for items
        # rebuilt by ``MediaItem(**entry)`` from a stored file too, not only for
        # the ones written through the repository. That is what makes a
        # hand-edited ``items.json`` self-repair on load instead of carrying a
        # slice the engines cannot use.
        self.slice = normalize_slice(self.slice)

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