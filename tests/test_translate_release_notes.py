import copy
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from consolidate_release import ReleaseError
from translate_release_notes import translate


class ReleaseTranslationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.release = dict(id=12, tag_name="v0.3.2", target_commitish="abc",
                            draft=False, prerelease=False, name="Old", body="Old",
                            assets=[dict(id=8, name="app.exe", size=123, digest="sha256:abc")])
        self.gh = Mock()
        self.gh.list_releases.return_value = [copy.deepcopy(self.release)]
        self.gh.release.side_effect = lambda _: copy.deepcopy(self.release)
        self.gh.edit.side_effect = lambda _, **fields: self.release.update(fields)

    def test_only_text_is_modified_and_assets_preserved(self):
        before = copy.deepcopy(self.release)
        with redirect_stdout(io.StringIO()):
            translate(self.gh, self.root)
        self.assertEqual(set(self.gh.edit.call_args.kwargs), {"name", "body"})
        for key in ("assets", "tag_name", "target_commitish", "draft", "prerelease"):
            self.assertEqual(self.release[key], before[key])
        self.assertIn("30 more distributions", self.release["body"])

    def test_detects_concurrent_visibility_change_before_edit(self):
        self.release["draft"] = True
        with self.assertRaisesRegex(ReleaseError, "changed before"):
            translate(self.gh, self.root)
        self.gh.edit.assert_not_called()

    def test_detects_unsaved_translation(self):
        self.gh.edit.side_effect = None
        with self.assertRaisesRegex(ReleaseError, "not saved"):
            translate(self.gh, self.root)

    def test_unknown_release_is_not_modified(self):
        self.gh.list_releases.return_value[0]["tag_name"] = "unknown"
        with self.assertRaisesRegex(ReleaseError, "No documented"):
            translate(self.gh, self.root)
        self.gh.edit.assert_not_called()
