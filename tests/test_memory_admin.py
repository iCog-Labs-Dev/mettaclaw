"""Unit tests for src/memory_admin.py using a fake in-memory collection
(no chromadb / network required).

Run:  python -m unittest tests.test_memory_admin
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import memory_admin as ma  # noqa: E402


class FakeCollection:
    """Minimal duck-typed stand-in for a chromadb collection (insertion order)."""

    def __init__(self, name="memories", rows=None):
        self.name = name
        self._rows = list(rows or [])  # list of (id, doc, meta)

    def count(self):
        return len(self._rows)

    def get(self, ids=None, limit=None, offset=None, include=None):
        if ids is not None:
            sel = [r for r in self._rows if r[0] in ids]
        else:
            start = offset or 0
            sel = self._rows[start: start + limit if limit is not None else None]
        return {
            "ids": [r[0] for r in sel],
            "documents": [r[1] for r in sel],
            "metadatas": [r[2] for r in sel],
        }

    def delete(self, ids=None):
        self._rows = [r for r in self._rows if r[0] not in (ids or [])]


class FakeClient:
    def __init__(self, collections):
        self._cols = {c.name: c for c in collections}

    def list_collections(self):
        return list(self._cols.values())

    def delete_collection(self, name):
        self._cols.pop(name, None)

    def get_or_create_collection(self, name):
        return self._cols.setdefault(name, FakeCollection(name))


def make_collection(n=3):
    rows = [(f"id{i}", f"memory number {i}", {"timestamp": f"2026-06-13 0{i}:00:00"})
            for i in range(1, n + 1)]
    return FakeCollection("memories", rows)


class MemoryAdminTests(unittest.TestCase):
    def setUp(self):
        self.col = make_collection(3)
        self.client = FakeClient([self.col])

    def test_recent_records_newest_first(self):
        recs, total = ma.recent_records(self.col)
        self.assertEqual(total, 3)
        self.assertEqual([r["id"] for r in recs], ["id3", "id2", "id1"])
        self.assertEqual(recs[0]["timestamp"], "2026-06-13 03:00:00")

    def test_empty_collection(self):
        recs, total = ma.recent_records(FakeCollection("memories"))
        self.assertEqual((recs, total), ([], 0))

    def test_get_record(self):
        self.assertIsNone(ma.get_record(self.col, "nope"))
        rec = ma.get_record(self.col, "id2")
        self.assertEqual(rec["doc"], "memory number 2")

    def test_delete_record(self):
        self.assertFalse(ma.delete_record(self.col, "nope"))
        self.assertTrue(ma.delete_record(self.col, "id2"))
        self.assertEqual(self.col.count(), 2)
        self.assertIsNone(ma.get_record(self.col, "id2"))

    def test_stats_and_purge(self):
        s = ma.stats(self.client, self.col)
        self.assertEqual(s["count"], 3)
        self.assertIn("memories", s["collections"])
        ma.purge(self.client, self.col)
        # purge recreates an empty collection of the same name
        _, fresh = None, self.client.get_or_create_collection("memories")
        self.assertEqual(fresh.count(), 0)

    def test_resolve_record_fingerprint_survives_shift(self):
        recs, _ = ma.recent_records(self.col)        # [id3,id2,id1]
        target = recs[1]                              # id2
        fp = ma.fingerprint(target["doc"])
        # Simulate a concurrent insert -> positions shift (new record at front).
        self.col._rows.append(("id4", "brand new memory", {"timestamp": "2026-06-13 04:00:00"}))
        recs2, _ = ma.recent_records(self.col)        # [id4,id3,id2,id1]
        # Old pos 1 now points at id3, but fingerprint still finds id2.
        resolved = ma.resolve_record(recs2, 1, fp)
        self.assertEqual(resolved["id"], "id2")

    def test_resolve_record_gone(self):
        recs, _ = ma.recent_records(self.col)
        self.assertIsNone(ma.resolve_record(recs, 1, "deadbeef"))

    def test_resolve_db_path(self):
        self.assertEqual(ma.resolve_db_path("/abs/chroma", "/repo"), "/abs/chroma")
        self.assertEqual(ma.resolve_db_path("./chroma_db", "/repo"), "/repo/chroma_db")

    def test_views_and_pagination(self):
        recs, total = ma.recent_records(self.col)
        page_recs = ma.format_list_page(recs, total, 1, page_size=2)
        self.assertEqual(len(page_recs), 2)
        text = ma.list_page_text(page_recs, 0, total, 1, page_size=2)
        self.assertIn("page 1/2", text)
        self.assertIn("🧠 Memory", ma.format_record(recs[0], 0))

    def test_callback_roundtrip_and_limits(self):
        self.assertEqual(ma.parse_callback(ma.cb("list", 2)), ("list", "2"))
        self.assertIsNone(ma.parse_callback("hist:menu"))
        for rows in (ma.menu_buttons(), ma.purge_confirm_buttons(),
                     ma.delete_confirm_buttons(3, "1a2b3c4d"), ma.record_buttons(3)):
            for row in rows:
                for _label, data in row:
                    self.assertLessEqual(len(data.encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main()
