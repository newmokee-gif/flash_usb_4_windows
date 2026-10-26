"""The bounded retry controller never mutates release contents."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recovery_schedule as schedule


NOW = datetime(2026, 10, 26, 18, 42, tzinfo=timezone.utc)


def tag():
    return {"object": {"type": "commit", "sha": schedule.SOURCE_SHA}}


def complete_release():
    return {"tag_name": schedule.TAG, "draft": False,
            "assets": [{"name": name, "state": "uploaded", "size": 123}
                       for name in sorted(schedule.EXPECTED_APPLICATIONS)]}


class DecisionTests(unittest.TestCase):
    def test_no_release_within_window_continues(self):
        decision = schedule.decide(tag(), [], NOW)
        self.assertTrue(decision.proceed)
        self.assertFalse(decision.disable)

    def test_old_release_does_not_prevent_recovery(self):
        self.assertTrue(schedule.decide(tag(), [{"tag_name": "v0.2.1", "draft": False}], NOW).proceed)

    def test_complete_release_stops_own_schedule(self):
        self.assertEqual(len(schedule.EXPECTED_APPLICATIONS), 8)
        decision = schedule.decide(tag(), [complete_release()], NOW)
        self.assertFalse(decision.proceed)
        self.assertTrue(decision.disable)
        self.assertFalse(decision.error)

    def test_existing_draft_waits_without_mutation(self):
        decision = schedule.decide(tag(), [{"tag_name": schedule.TAG, "draft": True}], NOW)
        self.assertFalse(decision.proceed)
        self.assertFalse(decision.disable)
        self.assertFalse(decision.error)

    def test_deadline_stops_missing_release_or_draft(self):
        for releases in ([], [{"tag_name": schedule.TAG, "draft": True}]):
            with self.subTest(releases=releases):
                decision = schedule.decide(tag(), releases, schedule.DEADLINE)
                self.assertFalse(decision.proceed)
                self.assertTrue(decision.disable)
                self.assertTrue(decision.error)
                self.assertIn("manual attention", decision.message)

    def test_complete_release_after_deadline_stops_successfully(self):
        decision = schedule.decide(tag(), [complete_release()], schedule.DEADLINE + timedelta(hours=1))
        self.assertTrue(decision.disable)
        self.assertFalse(decision.error)

    def test_changed_tag_stops_even_for_complete_publication(self):
        for obj in ({"type": "commit", "sha": "a" * 40},
                    {"type": "tag", "sha": schedule.SOURCE_SHA}):
            with self.subTest(obj=obj):
                decision = schedule.decide({"object": obj}, [complete_release()], NOW)
                self.assertTrue(decision.disable)
                self.assertTrue(decision.error)

    def test_incomplete_or_uploading_or_empty_application_stops(self):
        releases = []
        missing = complete_release()
        missing["assets"].pop()
        releases.append(missing)
        for field, value in (("state", "new"), ("size", 0), ("size", True)):
            invalid = complete_release()
            invalid["assets"][0][field] = value
            releases.append(invalid)
        duplicate = complete_release()
        duplicate["assets"].append(dict(duplicate["assets"][0]))
        releases.append(duplicate)
        for release in releases:
            with self.subTest(assets=release["assets"]):
                decision = schedule.decide(tag(), [release], NOW)
                self.assertTrue(decision.disable)
                self.assertTrue(decision.error)


class ControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        annotations = patch.object(schedule, "annotate")
        annotations.start()
        self.addCleanup(annotations.stop)
        self.output = Path(temporary.name) / "output"
        self.gh = Mock()
        self.pages = [[]]
        self.ref = tag()

        def api(suffix, **kwargs):
            if suffix == f"git/ref/tags/{schedule.TAG}":
                return self.ref
            if suffix == "releases?per_page=100":
                self.assertTrue(kwargs["paginate"])
                return self.pages
            if suffix == f"actions/workflows/{schedule.WORKFLOW_FILE}/disable":
                self.assertEqual(kwargs, {"method": "PUT"})
                return None
            raise AssertionError(f"Unexpected external operation: {suffix}")

        self.gh.api.side_effect = api

    def mutations(self):
        return [call for call in self.gh.api.call_args_list if call.kwargs.get("method", "GET") != "GET"]

    def test_ready_state_outputs_true_without_mutations(self):
        schedule.control(self.gh, NOW, self.output)
        self.assertTrue(self.output.read_text().endswith("proceed=true\n"))
        self.assertEqual(self.mutations(), [])

    def test_paginated_complete_release_disables_only_fixed_workflow(self):
        self.pages = [[{"tag_name": "v0.2.1"}], [complete_release()]]
        schedule.control(self.gh, NOW, self.output)
        self.assertEqual(self.output.read_text(), "proceed=false\n")
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.mutations()[0].args, ("actions/workflows/release-recovery.yml/disable",))

    def test_draft_does_not_disable_or_change_any_release(self):
        self.pages = [[{"tag_name": schedule.TAG, "draft": True}]]
        schedule.control(self.gh, NOW, self.output)
        self.assertEqual(self.output.read_text(), "proceed=false\n")
        self.assertEqual(self.mutations(), [])

    def test_cutoff_disables_own_workflow_and_raises(self):
        with self.assertRaisesRegex(schedule.ScheduleError, "deadline"):
            schedule.control(self.gh, schedule.DEADLINE, self.output)
        self.assertEqual(len(self.mutations()), 1)
        self.assertEqual(self.output.read_text(), "proceed=false\n")

    def test_tag_mismatch_disables_own_workflow_and_raises(self):
        self.ref["object"]["sha"] = "b" * 40
        with self.assertRaisesRegex(schedule.ScheduleError, "tag differs"):
            schedule.control(self.gh, NOW, self.output)
        self.assertEqual(len(self.mutations()), 1)

    def test_missing_inventory_is_not_treated_as_no_release(self):
        self.pages = []
        with self.assertRaisesRegex(schedule.ScheduleError, "inventory"):
            schedule.control(self.gh, NOW, self.output)
        self.assertEqual(self.mutations(), [])

    def test_other_workflow_context_fails_before_api_access(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": schedule.REPOSITORY,
                                     "GITHUB_WORKFLOW_REF": "another/workflow@refs/heads/main"}), \
                patch.object(schedule, "GitHub") as github:
            with self.assertRaisesRegex(schedule.ScheduleError, "only"):
                schedule.main()
        github.assert_not_called()


if __name__ == "__main__":
    unittest.main()
