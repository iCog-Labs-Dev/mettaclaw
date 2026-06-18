"""Unit tests for src/history_admin.py (pure logic, no Telegram/network).

Run:  python -m unittest tests.test_history_admin   (from repo root)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import history_admin as ha  # noqa: E402


# Two entries in the exact shape addToHistory serializes into history.metta.
SAMPLE = (
    '("2026-06-13 12:00:00"\n'
    ' "HUMAN_MESSAGE: " "hello there"\n'
    ' ((send hi)))\n'
    '\n'
    '("2026-06-13 12:05:30"\n'
    ' "HUMAN_MESSAGE: " "what is the weather"\n'
    ' ((search weather) (send sunny)))\n'
)


class HistoryAdminTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".metta")
        os.close(fd)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)

    def tearDown(self):
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

    def test_read_entries(self):
        entries = ha.read_entries(self.path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].index, 1)
        self.assertEqual(entries[0].timestamp, "2026-06-13 12:00:00")
        self.assertEqual(entries[1].timestamp, "2026-06-13 12:05:30")
        self.assertIn("hello there", entries[0].raw)

    def test_missing_and_empty_file(self):
        self.assertEqual(ha.read_entries("/no/such/file.metta"), [])
        open(self.path, "w").close()
        self.assertEqual(ha.read_entries(self.path), [])

    def test_stats(self):
        s = ha.history_stats(self.path)
        self.assertEqual(s["entries"], 2)
        self.assertTrue(s["exists"])
        self.assertEqual(s["first_timestamp"], "2026-06-13 12:00:00")
        self.assertEqual(s["last_timestamp"], "2026-06-13 12:05:30")
        self.assertGreater(s["size_bytes"], 0)

    def test_get_entry_bounds(self):
        self.assertIsNone(ha.get_entry(self.path, 0))
        self.assertIsNone(ha.get_entry(self.path, 99))
        self.assertEqual(ha.get_entry(self.path, 2).timestamp, "2026-06-13 12:05:30")

    def test_delete_entry(self):
        removed = ha.delete_entry(self.path, 1)
        self.assertEqual(removed.timestamp, "2026-06-13 12:00:00")
        remaining = ha.read_entries(self.path)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].timestamp, "2026-06-13 12:05:30")
        # re-indexed after rewrite
        self.assertEqual(remaining[0].index, 1)

    def test_delete_out_of_range(self):
        self.assertIsNone(ha.delete_entry(self.path, 5))
        self.assertEqual(len(ha.read_entries(self.path)), 2)

    def test_fingerprint_stable(self):
        e = ha.get_entry(self.path, 1)
        self.assertEqual(ha.fingerprint(e.raw), ha.fingerprint(e.raw))
        self.assertNotEqual(
            ha.fingerprint(ha.get_entry(self.path, 1).raw),
            ha.fingerprint(ha.get_entry(self.path, 2).raw),
        )

    def test_delete_by_content_preserves_concurrent_append(self):
        # Admin views entry #2 and captures its fingerprint.
        target = ha.get_entry(self.path, 2)
        fp = ha.fingerprint(target.raw)
        # Engine appends a NEW entry before the admin confirms the delete.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('\n("2026-06-13 13:00:00" ((send "live append")))\n')
        self.assertEqual(len(ha.read_entries(self.path)), 3)
        # Delete by fingerprint: removes exactly the viewed entry, keeps the
        # concurrently-appended one (no lost append, no wrong deletion).
        removed = ha.delete_entry(self.path, 2, fp)
        self.assertEqual(removed.timestamp, target.timestamp)
        remaining = [e.timestamp for e in ha.read_entries(self.path)]
        self.assertEqual(remaining, ["2026-06-13 12:00:00", "2026-06-13 13:00:00"])

    def test_delete_by_content_wrong_index_still_correct(self):
        # Fingerprint of entry #1, but caller passes a stale/wrong index (2).
        e1 = ha.get_entry(self.path, 1)
        fp = ha.fingerprint(e1.raw)
        removed = ha.delete_entry(self.path, 2, fp)  # index mismatch -> search by fp
        self.assertEqual(removed.timestamp, e1.timestamp)
        self.assertEqual([e.timestamp for e in ha.read_entries(self.path)],
                         ["2026-06-13 12:05:30"])

    def test_delete_by_content_gone_returns_none(self):
        removed = ha.delete_entry(self.path, 1, "deadbeef")  # no such fingerprint
        self.assertIsNone(removed)
        self.assertEqual(len(ha.read_entries(self.path)), 2)  # nothing deleted

    def test_purge(self):
        self.assertEqual(ha.purge(self.path), 2)
        self.assertEqual(ha.read_entries(self.path), [])
        self.assertTrue(os.path.exists(self.path))  # file kept, emptied

    def test_pagination(self):
        self.assertEqual(ha.page_count(0), 1)
        self.assertEqual(ha.page_count(5, 5), 1)
        self.assertEqual(ha.page_count(6, 5), 2)
        text, page_entries = ha.format_list_page(self.path, 1, page_size=1)
        self.assertEqual(len(page_entries), 1)
        # newest-first ordering
        self.assertEqual(page_entries[0].timestamp, "2026-06-13 12:05:30")
        self.assertIn("page 1/2", text)

    def test_callback_roundtrip(self):
        self.assertEqual(ha.parse_callback(ha.cb("list", 3)), ("list", "3"))
        self.assertEqual(ha.parse_callback(ha.cb("menu")), ("menu", None))
        self.assertIsNone(ha.parse_callback("other:thing"))

    def test_callback_data_within_limit(self):
        for rows in (
            ha.menu_buttons(),
            ha.purge_confirm_buttons(),
            ha.delete_confirm_buttons(123, "1a2b3c4d"),
            ha.entry_buttons(123),
        ):
            for row in rows:
                for _label, data in row:
                    self.assertLessEqual(len(data.encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main()
