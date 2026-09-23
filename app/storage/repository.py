"""JSON-backed persistence for the media registry."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from app.core.config import ITEMS_FILE
from app.core.models import MediaItem


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
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [MediaItem(**entry) for entry in entries]
        except (json.JSONDecodeError, TypeError, ValueError):
            # Never let a corrupt file kill the app: keep the original bytes
            # for manual recovery and start with an empty list.
            backup = self.path.with_suffix(".json.corrupt")
            backup.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
            self._items = []

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