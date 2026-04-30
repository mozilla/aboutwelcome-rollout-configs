#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from rollout import append_ledger, build_diff_summary, deep_diff


class TestDeepDiff(unittest.TestCase):

    def test_identical_returns_empty(self):
        self.assertEqual(deep_diff({"a": 1}, {"a": 1}), [])

    def test_scalar_change(self):
        result = deep_diff("old", "new", "field")
        self.assertEqual(result, ['`field`: `"old"` → `"new"`'])

    def test_dict_added_key(self):
        result = deep_diff({}, {"x": 1}, "root")
        self.assertIn("`root.x`: added `1`", result)

    def test_dict_removed_key(self):
        result = deep_diff({"x": 1}, {}, "root")
        self.assertIn("`root.x`: removed", result)

    def test_nested_dict_change(self):
        old = {"content": {"title": "old title"}}
        new = {"content": {"title": "new title"}}
        result = deep_diff(old, new)
        self.assertIn('`content.title`: `"old title"` → `"new title"`', result)

    def test_type_change(self):
        result = deep_diff({"key": "val"}, ["val"], "field")
        self.assertEqual(len(result), 1)
        self.assertIn("field", result[0])

    def test_list_without_ids_treated_as_scalar(self):
        result = deep_diff([1, 2], [1, 3], "items")
        self.assertEqual(len(result), 1)
        self.assertIn("items", result[0])

    def test_list_with_ids_matches_by_id(self):
        old = [{"id": "A", "value": 1}, {"id": "B", "value": 2}]
        new = [{"id": "A", "value": 99}, {"id": "C", "value": 3}]
        result = deep_diff(old, new, "items")
        joined = "\n".join(result)
        self.assertIn("items[A]", joined)    # A was modified
        self.assertIn("items[B]", joined)    # B was removed
        self.assertIn("items[C]", joined)    # C was added

    def test_list_item_added(self):
        old = [{"id": "A"}]
        new = [{"id": "A"}, {"id": "B"}]
        result = deep_diff(old, new, "items")
        self.assertIn("`items[B]`: added", result)

    def test_list_item_removed(self):
        old = [{"id": "A"}, {"id": "B"}]
        new = [{"id": "A"}]
        result = deep_diff(old, new, "items")
        self.assertIn("`items[B]`: removed", result)

    def test_long_value_truncated(self):
        long_str = "x" * 200
        result = deep_diff(long_str, "short", "field")
        self.assertEqual(len(result), 1)
        self.assertIn("…", result[0])

    def test_no_path_prefix(self):
        result = deep_diff({"a": 1}, {"a": 2})
        self.assertIn("`a`: `1` → `2`", result)


class TestBuildDiffSummary(unittest.TestCase):

    def _rollout(self, rollout_id="test:treatment", screens=None):
        return {
            "id": rollout_id,
            "transitions": True,
            "screens": screens or [],
        }

    def _screen(self, screen_id, **content):
        return {"id": screen_id, "content": content}

    def test_no_changes(self):
        data = self._rollout()
        self.assertEqual(build_diff_summary(data, data), [])

    def test_rollout_id_change(self):
        old = self._rollout("old-rollout:treatment")
        new = self._rollout("new-rollout:treatment")
        result = build_diff_summary(old, new)
        self.assertTrue(any("id" in line for line in result))

    def test_screen_added(self):
        old = self._rollout(screens=[])
        new = self._rollout(screens=[self._screen("AW_NEW")])
        result = build_diff_summary(old, new)
        self.assertTrue(any("AW_NEW" in line and "added" in line for line in result))

    def test_screen_removed(self):
        old = self._rollout(screens=[self._screen("AW_OLD")])
        new = self._rollout(screens=[])
        result = build_diff_summary(old, new)
        self.assertTrue(any("AW_OLD" in line and "removed" in line for line in result))

    def test_screen_field_changed(self):
        old = self._rollout(screens=[self._screen("AW_X", title="old")])
        new = self._rollout(screens=[self._screen("AW_X", title="new")])
        result = build_diff_summary(old, new)
        joined = "\n".join(result)
        self.assertIn("AW_X", joined)
        self.assertIn("title", joined)

    def test_screen_unchanged_not_reported(self):
        screen = self._screen("AW_SAME", title="same")
        old = self._rollout(screens=[screen])
        new = self._rollout(screens=[screen])
        result = build_diff_summary(old, new)
        self.assertFalse(any("AW_SAME" in line for line in result))

    def test_top_level_field_added(self):
        old = self._rollout()
        new = {**self._rollout(), "new_field": True}
        result = build_diff_summary(old, new)
        self.assertTrue(any("new_field" in line for line in result))

    def test_top_level_field_removed(self):
        old = {**self._rollout(), "old_field": True}
        new = self._rollout()
        result = build_diff_summary(old, new)
        self.assertTrue(any("old_field" in line for line in result))


class TestAppendLedger(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_path = Path(self.tmp.name) / "260429-0-test-treatment.json"
        self.archive_path.touch()
        self.ledger_path = Path(self.tmp.name) / "ledger.md"

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, diff_lines=None):
        import rollout
        original = rollout.Path
        rollout.Path = lambda p: self.ledger_path if p == "ledger.md" else original(p)
        try:
            append_ledger(self.archive_path, "test:treatment", diff_lines or [])
        finally:
            rollout.Path = original

    def test_creates_file_with_header(self):
        self._call(["- `id`: added"])
        content = self.ledger_path.read_text()
        self.assertIn("# Rollout Change Ledger", content)
        self.assertIn("test:treatment", content)
        self.assertIn("260429-0-test-treatment.json", content)

    def test_prepends_on_second_call(self):
        self._call(["- first change"])
        self._call(["- second change"])
        content = self.ledger_path.read_text()
        self.assertLess(content.index("second change"), content.index("first change"))

    def test_no_changes_message(self):
        self._call([])
        self.assertIn("No content changes detected", self.ledger_path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
