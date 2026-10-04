"""JSON-backed persistence for the media registry."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from app.core.config import ITEMS_FILE
from app.core.models import MediaItem, normalize_slice

LEGACY_MARKS_KEY = "marks"


def _slice_from_legacy_marks(entry):
    """``entry`` with the retired ``marks`` key turned into a ``slice`` key.

    ``MediaItem`` no longer has a ``marks`` field, so a file saved before that
    field was retired makes ``MediaItem(**entry)`` raise ``TypeError``. That is
    not a crash: the broad ``except`` that :meth:`MediaRepository.load` used
    to wrap the whole comprehension swallowed it, the library loaded EMPTY, the
    original bytes were moved aside, and ``save()`` then overwrote the file with
    the empty list. Measured on a three-entry file that all carried ``marks``,
    before this translation existed: 0 entries loaded, ``items.json.corrupt``
    written, ``items.json`` left holding ``[]``. So the key has to be consumed
    here, per entry, before the dataclass ever sees it.

    The legacy shape was a LIST of pairs, and there is one pair now:

    * ``marks`` absent, or ``null`` -- nothing to translate, the slice stays
      ``None``. Every real media in ``data/items.json`` measured as
      ``marks: null``, so the file shipping today does not reach this path.
    * exactly one usable pair -- that pair is the slice. It is the shape the
      old UI produced most often, and it means the same thing to the user.
    * zero pairs, or two or more -- the slice becomes ``None``. Unioning the
      pairs would transcribe MORE than the user asked for and taking the first
      would transcribe LESS; both are guesses about a shape this redesign no
      longer has, so dropping it is the only honest reading.

    A ``slice`` already present wins over ``marks``, and ``marks`` is dropped
    either way: that covers a file caught mid-migration, where both keys can sit
    in the same entry.
    """
    if not isinstance(entry, dict) or LEGACY_MARKS_KEY not in entry:
        return entry
    migrated = dict(entry)
    legacy = migrated.pop(LEGACY_MARKS_KEY)
    if "slice" not in migrated:
        if isinstance(legacy, (list, tuple)) and len(legacy) == 1:
            migrated["slice"] = normalize_slice(legacy[0])
        else:
            migrated["slice"] = None
    return migrated


class MediaRepository:
    """Loads and persists the media list.

    The first use creates an empty list file, so the app always starts with a
    usable registry even when ``data/items.json`` does not exist yet.
    """

    def __init__(self, path: Path = ITEMS_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: List[MediaItem] = []
        self.load()
        self.save()  # ensure the file exists for a brand-new list

    def load(self) -> None:
        if not self.path.exists():
            self._items = []
            return
        text = self.path.read_text(encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self._set_aside(text)
            self._items = []
            return
        if not isinstance(payload, list):
            # A JSON object where a list of entries was expected used to reach
            # a single broad except and cost the whole library, which is the
            # wrong price for a shape this class cannot use.
            self._set_aside(text)
            self._items = []
            return
        # Entries are built one at a time, each with its own guard: a single row
        # that cannot become a MediaItem must cost that row only, not the rest
        # of the library. The old single try around the whole comprehension
        # could not tell "this file is unreadable" from "one entry is bad", and
        # answered both by emptying everything.
        items: List[MediaItem] = []
        dropped = 0
        for entry in payload:
            try:
                items.append(MediaItem(**_slice_from_legacy_marks(entry)))
            except (TypeError, ValueError):
                dropped += 1
        if dropped:
            # Dropping a row loses data, so the raw bytes are kept for manual
            # recovery exactly as they are for an unreadable file. ``save()``
            # overwrites the file right after this, so this copy is the only
            # one left of the dropped entries.
            self._set_aside(text)
        self._items = items

    def _set_aside(self, text: str) -> None:
        """Keep the original file bytes aside for manual recovery.

        Never let a file this class cannot use kill the app, and never let it
        disappear either: ``__init__`` calls :meth:`save` right after
        :meth:`load`, so whatever is not read is overwritten.
        """
        backup = self.path.with_suffix(".json.corrupt")
        backup.write_text(text, encoding="utf-8")

    def save(self) -> None:
        payload = [asdict(item) for item in self._items]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def all(self) -> List[MediaItem]:
        return list(self._items)

    def get(self, index: int) -> MediaItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def index_of(self, path: str) -> int | None:
        """Index of ``path`` (case-insensitive, same normalization as
        :meth:`add`), or None when the file is not registered."""
        normalized = str(Path(path))
        for i, item in enumerate(self._items):
            if item.path.lower() == normalized.lower():
                return i
        return None

    def update_results(
        self,
        index: int,
        *,
        transcript_text=None,
        transcript_lang=None,
        transcript_segments=None,
        translation_text=None,
        translation_lang=None,
        translation_segments=None,
    ) -> None:
        """Set ONLY the provided (non-None) result fields on the item at
        ``index`` and persist. Fields left at None keep their stored value."""
        item = self._items[index]
        updates = {
            "transcript_text": transcript_text,
            "transcript_lang": transcript_lang,
            "transcript_segments": transcript_segments,
            "translation_text": translation_text,
            "translation_lang": translation_lang,
            "translation_segments": translation_segments,
        }
        for name, value in updates.items():
            if value is not None:
                setattr(item, name, value)
        self.save()

    def update_edited_text(
        self,
        index: int,
        *,
        transcript_text: str | None = None,
        translation_text: str | None = None,
    ) -> None:
        """Persist user-edited pane text without changing base results.

        ``None`` means "do not update this side". An empty string is a real
        user edit and is therefore stored, allowing a user to clear a pane.
        """
        item = self._items[index]
        if transcript_text is not None:
            item.transcript_edited_text = transcript_text
        if translation_text is not None:
            item.translation_edited_text = translation_text
        self.save()

    def clear_edited_text(
        self,
        index: int,
        *,
        transcript: bool = True,
        translation: bool = True,
    ) -> None:
        """Remove edited-note state from the selected pane sides.

        Base text, language metadata, and timed segments are intentionally
        left untouched. This operation is used only after a successful run
        replaces the corresponding result.
        """
        item = self._items[index]
        if transcript:
            item.transcript_edited_text = None
        if translation:
            item.translation_edited_text = None
        self.save()

    def update_slice(self, index: int, value) -> None:
        """Replace the slice of the item at ``index`` and persist.

        ``value`` is anything a caller might hold: a ``[start, end]`` pair, a
        tuple of slider positions, ``None``. It is normalized before being
        stored, so raw slider positions can be passed straight through, and an
        empty or invalid value is stored as ``None`` -- the same "whole file"
        state as never having set a slice, and never an empty range list.

        Unlike the in/out marks it replaces, this is not consumed by a run: the
        slice persists and is re-read by the next one.
        """
        item = self._items[index]
        item.slice = normalize_slice(value)
        self.save()

    def add(self, path: str) -> MediaItem:
        """Register a file; duplicate paths (case-insensitive) are ignored."""
        normalized = str(Path(path))
        for item in self._items:
            if item.path.lower() == normalized.lower():
                return item
        item = MediaItem.from_path(normalized)
        self._items.append(item)
        self.save()
        return item

    def update_alias(self, index: int, alias: str) -> None:
        self._items[index].alias = alias.strip()
        self.save()

    def update_annotation(self, index: int, annotation: str) -> None:
        self._items[index].annotation = annotation
        self.save()

    def remove(self, index: int) -> None:
        del self._items[index]
        self.save()