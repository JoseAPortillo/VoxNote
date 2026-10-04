"""Tests for the single slice: its normalization and its persistence.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

or under pytest, which collects the same ``unittest.TestCase`` classes. The
``unittest`` style of ``tests/asr/`` is kept on purpose rather than reaching for
pytest fixtures: every case here is pure logic over a temporary file, so the
suite has to stay runnable by the stdlib runner the rest of the tests document.

The slice replaced a LIST of in/out marks (the legacy ``marks`` key). So the
invariants that matter are as much about the ABSENCE of a slice as about its
presence: the transcription engines read an empty range list as "the whole
file", so "no slice" must be ``None`` and must never be stored as ``[]``. Each
rejection case below exists because a value that SURVIVED normalization would
silently widen or narrow what gets transcribed -- the failure mode this work is
about, not a tidiness concern.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from app.core.models import MediaItem, normalize_slice
from app.storage.repository import MediaRepository

TS = "2026-10-04T12:00:00"


def item(path: str = "clip.wav", **fields_) -> MediaItem:
    """A minimal valid item; ``fields_`` overrides any default."""
    return MediaItem(path=path, alias=Path(path).stem, added_at=TS, **fields_)


class TestNormalizeSlice(unittest.TestCase):
    """One pair in, one pair out -- or ``None``, never an empty list."""

    def test_keeps_a_valid_pair(self):
        self.assertEqual(normalize_slice([1.5, 9.0]), [1.5, 9.0])

    def test_a_zero_start_is_a_valid_slice(self):
        # 0 s is a real position, not an absent value: the whole-file contract
        # is carried by None, so there is nothing to confuse it with.
        self.assertEqual(normalize_slice([0.0, 1.0]), [0.0, 1.0])

    def test_accepts_a_tuple_and_returns_a_list(self):
        # JSON has no tuple: a tuple written to disk comes back from
        # json.loads as a LIST, so a slice stored as a tuple would compare
        # unequal after one save/load cycle. Storing lists makes the round trip
        # equal by value instead of by accident of type.
        result = normalize_slice((1.0, 5.0))
        self.assertIsInstance(result, list)
        self.assertEqual(result, [1.0, 5.0])

    def test_coerces_ints_and_numeric_strings_to_float(self):
        # The pair is stored as float either way, so a hand-edited items.json
        # with integers cannot leak an int into the JSON and fail a later
        # equality check against a float-built slice.
        result = normalize_slice(["1", 5])
        self.assertEqual(result, [1.0, 5.0])
        self.assertIsInstance(result[0], float)
        self.assertIsInstance(result[1], float)

    def test_none_means_no_slice(self):
        self.assertIsNone(normalize_slice(None))

    def test_an_empty_list_is_no_slice_and_not_an_empty_list(self):
        # THE landmine. The engines read an empty range list as "the whole
        # file", so returning [] here would store a state indistinguishable
        # from "no slice" while meaning the opposite to the engine.
        result = normalize_slice([])
        self.assertIsNone(result)

    def test_rejects_a_wrong_length(self):
        for raw in ([1.0], [1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0]):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_slice(raw))

    def test_rejects_a_bare_string(self):
        # "12" is iterable and float("1") / float("2") both succeed, so
        # indexing it would invent the slice [1.0, 2.0] out of two characters.
        for raw in ("12", "1,2", "0-5"):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_slice(raw))

    def test_rejects_a_dict(self):
        # Both the obvious shape and the accidental one: {"0": .., "1": ..}
        # indexes by KEY and would otherwise pass the length check.
        for raw in ({"start": 1.0, "end": 5.0}, {"0": 1.0, "1": 5.0}):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_slice(raw))

    def test_rejects_non_numeric_values(self):
        for raw in ([None, 5.0], ["a", "b"], [1.0, [2.0]]):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_slice(raw))

    def test_rejects_nan_and_infinity(self):
        # A NaN end would survive every ordering comparison, so it would
        # reach the engines as a range they cannot seek or decode.
        for raw in (
            [float("nan"), 5.0],
            [0.0, float("nan")],
            [0.0, float("inf")],
            [float("-inf"), 1.0],
            [1.0, float("-inf")],
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_slice(raw))

    def test_rejects_negative_values(self):
        self.assertIsNone(normalize_slice([-1.0, 5.0]))
        self.assertIsNone(normalize_slice([1.0, -5.0]))

    def test_rejects_an_empty_and_an_inverted_range(self):
        self.assertIsNone(normalize_slice([4.0, 4.0]))
        self.assertIsNone(normalize_slice([9.0, 1.0]))

    def test_rejects_nested_pairs(self):
        # [[1, 2]] is the SHAPE the old marks list used: a list of pairs.
        self.assertIsNone(normalize_slice([[1.0, 2.0]]))

    def test_rejects_booleans(self):
        # JSON true/false are not numbers a user could have meant as seconds;
        # float(True) is 1.0, so [false, true] would become a 0-1 s slice.
        for raw in ([True, False], [False, True], [False, 5.0]):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_slice(raw))

    def test_does_not_alias_the_callers_pair(self):
        # A slider passing its own mutable list must not be able to rewrite
        # the stored slice afterwards.
        raw = [1.0, 5.0]
        result = normalize_slice(raw)
        raw[1] = 99.0
        self.assertEqual(result, [1.0, 5.0])


class TestMediaItemSlice(unittest.TestCase):
    def test_defaults_to_no_slice(self):
        self.assertIsNone(item().slice)

    def test_post_init_normalizes_on_every_construction(self):
        self.assertEqual(item(slice=[3, 8]).slice, [3.0, 8.0])

    def test_post_init_repairs_a_hand_edited_entry(self):
        # A stored value the normalizer rejects must not survive the load.
        self.assertIsNone(item(slice=[8.0, 3.0]).slice)
        self.assertIsNone(item(slice=[]).slice)

    def test_the_marks_field_is_replaced_by_slice(self):
        names = {f.name for f in fields(MediaItem)}
        self.assertIn("slice", names)
        self.assertNotIn("marks", names)
        # Nothing may accept the retired key any more: a stray one has to be a
        # hard, visible failure rather than a silently ignored extra.
        with self.assertRaises(TypeError):
            MediaItem(path="a.wav", alias="a", added_at=TS, marks=[[1.0, 2.0]])


class SliceRepositoryCase(unittest.TestCase):
    """A real repository over a throwaway ``items.json``."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.items_file = Path(tmp.name) / "items.json"
        self.corrupt_file = Path(tmp.name) / "items.json.corrupt"

    def write_json(self, payload) -> None:
        self.items_file.write_text(json.dumps(payload), encoding="utf-8")

    def stored(self) -> list[dict]:
        return json.loads(self.items_file.read_text(encoding="utf-8"))


class TestSlicePersistence(SliceRepositoryCase):
    def test_a_slice_survives_a_save_and_a_reload(self):
        repo = MediaRepository(self.items_file)
        repo.add("a.wav")
        repo.update_slice(0, [12.5, 48.0])

        # Two fresh repositories, so the value is read from disk and not from
        # an object that still holds it in memory.
        self.assertEqual(MediaRepository(self.items_file).get(0).slice, [12.5, 48.0])
        self.assertEqual(MediaRepository(self.items_file).get(0).slice, [12.5, 48.0])

    def test_slice_is_persisted_under_its_own_key(self):
        repo = MediaRepository(self.items_file)
        repo.add("a.wav")
        repo.update_slice(0, [12.5, 48.0])
        entry = self.stored()[0]
        self.assertEqual(entry["slice"], [12.5, 48.0])
        self.assertNotIn("marks", entry)

    def test_update_slice_normalizes_before_storing(self):
        repo = MediaRepository(self.items_file)
        repo.add("a.wav")
        repo.update_slice(0, (3, 9))
        self.assertEqual(MediaRepository(self.items_file).get(0).slice, [3.0, 9.0])

    def test_an_invalid_or_absent_selection_is_stored_as_no_slice(self):
        # Deleting the slice is "no slice", never an empty range list.
        repo = MediaRepository(self.items_file)
        repo.add("a.wav")
        for value in ([9.0, 3.0], [], None, "12"):
            with self.subTest(value=value):
                repo.update_slice(0, [1.0, 2.0])
                repo.update_slice(0, value)
                self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_updating_one_slice_does_not_touch_another(self):
        repo = MediaRepository(self.items_file)
        repo.add("a.wav")
        repo.add("b.wav")
        repo.add("c.wav")
        repo.update_slice(1, [4.0, 7.0])

        reloaded = MediaRepository(self.items_file)
        self.assertEqual(len(reloaded.all()), 3)
        self.assertEqual([i.slice for i in reloaded.all()],
                         [None, [4.0, 7.0], None])
        self.assertEqual([i.path for i in reloaded.all()],
                         ["a.wav", "b.wav", "c.wav"])

    def test_a_cleared_slice_leaves_the_rest_of_the_item_intact(self):
        repo = MediaRepository(self.items_file)
        repo.add("a.wav")
        repo.update_annotation(0, "keep me")
        repo.update_slice(0, [1.0, 2.0])
        repo.update_slice(0, None)

        reloaded = MediaRepository(self.items_file).get(0)
        self.assertIsNone(reloaded.slice)
        self.assertEqual(reloaded.annotation, "keep me")


class TestLegacyMarksTranslation(SliceRepositoryCase):
    """A file saved before the redesign carries ``marks``, never ``slice``."""

    def legacy(self, path: str, marks) -> dict:
        return {"path": path, "alias": Path(path).stem, "added_at": TS, "marks": marks}

    def test_marks_null_becomes_no_slice(self):
        self.write_json([self.legacy("a.wav", None)])
        self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_exactly_one_pair_becomes_the_slice(self):
        self.write_json([self.legacy("a.wav", [[12.5, 48.0]])])
        self.assertEqual(MediaRepository(self.items_file).get(0).slice, [12.5, 48.0])

    def test_an_invalid_single_pair_becomes_no_slice(self):
        self.write_json([self.legacy("a.wav", [[48.0, 12.5]])])
        self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_zero_pairs_becomes_no_slice(self):
        self.write_json([self.legacy("a.wav", [])])
        self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_two_pairs_are_dropped_not_merged_and_not_truncated(self):
        # Unioning them would transcribe MORE than the user asked for and
        # taking the first would transcribe LESS; both are guesses about a
        # shape this redesign no longer has, so the slice is dropped instead.
        self.write_json([self.legacy("a.wav", [[1.0, 2.0], [3.0, 4.0]])])
        self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_a_flat_pair_is_not_read_as_one_pair(self):
        # The old shape was a list OF pairs; a flat [1, 2] is two malformed
        # entries, not one slice.
        self.write_json([self.legacy("a.wav", [1.0, 2.0])])
        self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_a_non_list_marks_value_becomes_no_slice(self):
        for marks in ("12", {"start": 1.0, "end": 5.0}, 12):
            with self.subTest(marks=marks):
                self.write_json([self.legacy("a.wav", marks)])
                self.assertIsNone(MediaRepository(self.items_file).get(0).slice)

    def test_a_file_where_every_entry_has_marks_keeps_every_entry(self):
        # The landmine this translation exists for: with ``marks`` no longer a
        # field, ``MediaItem(**entry)`` raises TypeError, the broad except in
        # load() swallows it, and the whole library loads EMPTY with the real
        # bytes moved aside. Measured on this file before the translation, the
        # library came back with zero entries and items.json.corrupt written.
        self.write_json([
            self.legacy("a.wav", [[1.0, 2.0]]),
            self.legacy("b.wav", None),
            self.legacy("c.wav", [[5.0, 9.5]]),
            self.legacy("d.wav", [[1.0, 2.0], [3.0, 4.0]]),
        ])
        repo = MediaRepository(self.items_file)

        self.assertEqual(len(repo.all()), 4)
        self.assertEqual([i.path for i in repo.all()],
                         ["a.wav", "b.wav", "c.wav", "d.wav"])
        self.assertEqual([i.slice for i in repo.all()],
                         [[1.0, 2.0], None, [5.0, 9.5], None])
        self.assertFalse(self.corrupt_file.exists())

    def test_an_entry_without_marks_still_loads(self):
        self.write_json([{"path": "a.wav", "alias": "a", "added_at": TS}])
        repo = MediaRepository(self.items_file)
        self.assertEqual(len(repo.all()), 1)
        self.assertIsNone(repo.get(0).slice)

    def test_a_translated_file_is_saved_back_without_marks(self):
        self.write_json([self.legacy("a.wav", [[12.5, 48.0]])])
        MediaRepository(self.items_file)
        entry = self.stored()[0]
        self.assertEqual(entry["slice"], [12.5, 48.0])
        self.assertNotIn("marks", entry)

    def test_one_unbuildable_entry_does_not_take_the_others_with_it(self):
        # Translation is per entry, and so is construction: a single bad row
        # must cost that row only.
        self.write_json([
            self.legacy("a.wav", [[1.0, 2.0]]),
            {"alias": "broken", "added_at": TS},  # no path -> TypeError
            self.legacy("c.wav", None),
        ])
        repo = MediaRepository(self.items_file)
        self.assertEqual([i.path for i in repo.all()], ["a.wav", "c.wav"])

    def test_a_dropped_entry_leaves_the_original_bytes_recoverable(self):
        # Dropping a row is data loss, so the raw file is kept for manual
        # recovery exactly as an unreadable file is.
        self.write_json([
            self.legacy("a.wav", [[1.0, 2.0]]),
            {"alias": "broken", "added_at": TS},
        ])
        MediaRepository(self.items_file)
        self.assertTrue(self.corrupt_file.exists())
        self.assertEqual(len(json.loads(self.corrupt_file.read_text(encoding="utf-8"))), 2)


if __name__ == "__main__":
    unittest.main()