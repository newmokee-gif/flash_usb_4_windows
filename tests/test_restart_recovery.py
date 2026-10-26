"""The one-off restart may replace only its pinned, unassigned recovery run."""
import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recover_multios as recovery
import recovery_schedule as schedule
import restart_recovery as restart


NOW = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)
HELPER_SHA = "f" * 40
WORKFLOW_ID = 1234
TARGET_PATH = f"actions/runs/{restart.TARGET_RUN}"
JOBS_PATH = TARGET_PATH + "/attempts/1/jobs?per_page=100"
RUNS_PATH = f"actions/workflows/{restart.WORKFLOW_FILE}/runs?branch=main&per_page=100"


def target_run(**changes):
    run = {
        "id": restart.TARGET_RUN, "run_attempt": 1, "head_sha": restart.TARGET_SHA,
        "head_branch": "main", "event": "push", "path": restart.WORKFLOW_PATH,
        "workflow_id": WORKFLOW_ID, "status": "in_progress", "conclusion": None,
        "repository": {"full_name": restart.REPOSITORY},
        "head_repository": {"full_name": restart.REPOSITORY},
    }
    run.update(changes)
    return run


def target_job(name, **changes):
    job = {
        "name": name, "run_id": restart.TARGET_RUN, "run_attempt": 1,
        "head_sha": restart.TARGET_SHA, "status": "queued", "conclusion": None,
        "runner_id": 0, "runner_name": "", "steps": [],
    }
    job.update(changes)
    return job


def target_jobs():
    return [target_job("control", status="completed", conclusion="success"),
            target_job("preflight", status="completed", conclusion="success"),
            target_job("apple-silicon")]


def complete_release():
    return {"tag_name": schedule.TAG, "draft": False,
            "assets": [{"name": name, "state": "uploaded", "size": 123}
                       for name in sorted(schedule.EXPECTED_APPLICATIONS)]}


class TargetAndJobTests(unittest.TestCase):
    def test_restart_is_pinned_to_the_authorized_original_attempt(self):
        self.assertEqual(restart.TARGET_RUN, 40380872693)
        self.assertEqual(restart.TARGET_SHA, "a06bd15e38828934516da067c4660daaf105f1ae")
        restart.validate_target(target_run(), WORKFLOW_ID)

    def test_target_provenance_mismatch_or_missing_field_is_rejected(self):
        alternatives = {
            "id": restart.TARGET_RUN + 1, "run_attempt": 2,
            "head_sha": "0" * 40, "head_branch": "other", "event": "workflow_dispatch",
            "path": ".github/workflows/other.yml", "workflow_id": WORKFLOW_ID + 1,
            "repository": {"full_name": "someone/fork"},
            "head_repository": {"full_name": "someone/fork"},
        }
        for field, value in alternatives.items():
            for missing in (False, True):
                with self.subTest(field=field, missing=missing):
                    run = target_run()
                    if missing:
                        del run[field]
                    else:
                        run[field] = value
                    with self.assertRaises(recovery.RecoveryError):
                        restart.validate_target(run, WORKFLOW_ID)

    def test_queued_arm_with_successful_preflight_and_control_is_safe(self):
        for status in ("queued", "in_progress"):
            for publisher in ([], [target_job("publish")]):
                with self.subTest(status=status, publisher=bool(publisher)):
                    self.assertTrue(restart.safe_to_cancel(
                        target_run(status=status), target_jobs() + publisher))

    def test_queue_timestamp_alone_does_not_prove_runner_assignment(self):
        jobs = target_jobs()
        jobs[-1].update(started_at="2026-10-26T19:59:00Z", runner_id=None,
                        runner_name=None, steps=None)
        self.assertTrue(restart.safe_to_cancel(target_run(), jobs))

    def test_inactive_or_unknown_run_status_is_unsafe(self):
        for status in ("completed", "waiting", "pending", "requested", None):
            with self.subTest(status=status):
                self.assertFalse(restart.safe_to_cancel(target_run(status=status), target_jobs()))

    def test_control_and_preflight_must_both_have_succeeded(self):
        for name in ("control", "preflight"):
            for changes in ({"status": "queued", "conclusion": None},
                            {"status": "in_progress", "conclusion": None},
                            {"conclusion": "failure"}, {"conclusion": "skipped"},
                            {"conclusion": None}):
                with self.subTest(name=name, changes=changes):
                    jobs = target_jobs()
                    next(job for job in jobs if job["name"] == name).update(changes)
                    self.assertFalse(restart.safe_to_cancel(target_run(), jobs))

    def test_missing_required_jobs_is_unsafe(self):
        for missing in ("control", "preflight", "apple-silicon"):
            with self.subTest(missing=missing):
                jobs = [job for job in target_jobs() if job["name"] != missing]
                self.assertFalse(restart.safe_to_cancel(target_run(), jobs))

    def test_assigned_started_or_completed_arm_and_publisher_are_unsafe(self):
        changes = ({"runner_id": 42}, {"runner_name": "assigned-runner"},
                   {"steps": [{"name": "Set up job", "status": "queued"}]},
                   {"status": "in_progress"}, {"status": "completed", "conclusion": "success"},
                   {"status": "completed", "conclusion": "cancelled"},
                   {"conclusion": "failure"})
        for name in ("apple-silicon", "publish"):
            for change in changes:
                with self.subTest(name=name, change=change):
                    jobs = target_jobs() + [target_job("publish")]
                    next(job for job in jobs if job["name"] == name).update(change)
                    self.assertFalse(restart.safe_to_cancel(target_run(), jobs))

    def test_unexpected_or_duplicate_job_is_rejected(self):
        for extra in (target_job("other"), target_job("apple-silicon")):
            with self.subTest(name=extra["name"]):
                with self.assertRaisesRegex(recovery.RecoveryError, "Unexpected or duplicate"):
                    restart.safe_to_cancel(target_run(), target_jobs() + [extra])

    def test_each_job_requires_the_exact_target_source(self):
        alternatives = {"run_id": restart.TARGET_RUN + 1,
                        "run_attempt": 2, "head_sha": "0" * 40}
        for name in ("control", "preflight", "apple-silicon", "publish"):
            for field, value in alternatives.items():
                for missing in (False, True):
                    with self.subTest(name=name, field=field, missing=missing):
                        jobs = target_jobs() + [target_job("publish")]
                        job = next(job for job in jobs if job["name"] == name)
                        if missing:
                            del job[field]
                        else:
                            job[field] = value
                        with self.assertRaisesRegex(recovery.RecoveryError, "job source"):
                            restart.safe_to_cancel(target_run(), jobs)


class RestartTests(unittest.TestCase):
    def setUp(self):
        annotations = patch.object(restart, "annotate")
        annotations.start()
        self.addCleanup(annotations.stop)
        self.gh = Mock(spec=["api", "collection"])
        self.run = target_run()
        self.run_responses = []
        self.jobs = target_jobs()
        self.runs = []
        self.workflow = {"id": WORKFLOW_ID, "path": restart.WORKFLOW_PATH, "state": "active"}
        self.main_ref = {"object": {"type": "commit", "sha": HELPER_SHA}}
        self.tag_ref = {"object": {"type": "commit", "sha": schedule.SOURCE_SHA}}
        self.release_pages = [[{"tag_name": "v0.2.1", "draft": False}], []]
        self.events = []
        self.after_cancel = lambda: self.run.update(status="completed", conclusion="cancelled")
        self.sleep = Mock()
        self.clock = Mock(return_value=NOW)
        self.dispatch = Mock(side_effect=lambda: self.events.append(("dispatch",)))

        def api(suffix, **kwargs):
            self.events.append(("api", suffix, kwargs))
            if suffix == f"actions/workflows/{restart.WORKFLOW_FILE}":
                self.assertEqual(kwargs, {})
                return copy.deepcopy(self.workflow)
            if suffix == "git/ref/heads/main":
                self.assertEqual(kwargs, {})
                return copy.deepcopy(self.main_ref)
            if suffix == f"git/ref/tags/{schedule.TAG}":
                self.assertEqual(kwargs, {})
                return copy.deepcopy(self.tag_ref)
            if suffix == "releases?per_page=100":
                self.assertEqual(kwargs, {"paginate": True})
                return copy.deepcopy(self.release_pages)
            if suffix == TARGET_PATH:
                self.assertEqual(kwargs, {})
                run = self.run_responses.pop(0) if self.run_responses else self.run
                self.events.append(("target", run.get("status"), run.get("conclusion")))
                return copy.deepcopy(run)
            if suffix == TARGET_PATH + "/cancel":
                self.assertEqual(kwargs, {"method": "POST"})
                self.after_cancel()
                return None
            raise AssertionError(f"Unexpected external operation: {suffix}")

        def collection(suffix, key):
            self.events.append(("collection", suffix, key))
            if (suffix, key) == (JOBS_PATH, "jobs"):
                return copy.deepcopy(self.jobs)
            if (suffix, key) == (RUNS_PATH, "workflow_runs"):
                return copy.deepcopy(self.runs)
            raise AssertionError(f"Unexpected collection: {suffix}, {key}")

        self.gh.api.side_effect = api
        self.gh.collection.side_effect = collection

    def invoke(self):
        return restart.restart(self.gh, HELPER_SHA, self.dispatch,
                               sleep=self.sleep, clock=self.clock)

    def mutations(self):
        return [item for item in self.gh.api.call_args_list
                if item.kwargs.get("method", "GET") != "GET"]

    def assert_no_changes(self):
        self.assertEqual(self.mutations(), [])
        self.dispatch.assert_not_called()

    def test_cancellation_must_be_confirmed_before_dispatch(self):
        self.assertEqual(self.invoke(), "dispatched")
        self.assertEqual(self.mutations(), [call(TARGET_PATH + "/cancel", method="POST")])
        self.dispatch.assert_called_once_with()
        cancel_index = self.events.index(("api", TARGET_PATH + "/cancel", {"method": "POST"}))
        confirmed_index = self.events.index(("target", "completed", "cancelled"))
        runs_index = self.events.index(("collection", RUNS_PATH, "workflow_runs"))
        dispatch_index = self.events.index(("dispatch",))
        self.assertLess(cancel_index, confirmed_index)
        self.assertLess(confirmed_index, runs_index)
        self.assertLess(runs_index, dispatch_index)
        self.gh.collection.assert_any_call(JOBS_PATH, "jobs")
        self.assertEqual(self.gh.api.call_args_list.count(
            call("releases?per_page=100", paginate=True)), 2)
        self.sleep.assert_not_called()

    def test_delayed_confirmation_is_polled_before_dispatch(self):
        self.after_cancel = lambda: None

        def finish_cancelling(_seconds):
            self.run.update(status="completed", conclusion="cancelled")

        self.sleep.side_effect = finish_cancelling
        self.assertEqual(self.invoke(), "dispatched")
        self.sleep.assert_called_once_with(5)
        self.dispatch.assert_called_once_with()

    def test_cancellation_timeout_never_dispatches(self):
        self.after_cancel = lambda: None
        with self.assertRaisesRegex(recovery.RecoveryError, "did not confirm cancellation"):
            self.invoke()
        self.assertEqual(self.sleep.call_count, 25)
        self.assertTrue(all(item == call(5) for item in self.sleep.call_args_list))
        self.dispatch.assert_not_called()
        self.assertNotIn(call(RUNS_PATH, "workflow_runs"), self.gh.collection.call_args_list)

    def test_natural_or_unknown_completion_after_cancel_never_dispatches(self):
        for conclusion in ("success", "failure", "timed_out", None):
            with self.subTest(conclusion=conclusion):
                self.run = target_run()
                self.after_cancel = lambda: self.run.update(status="completed", conclusion=conclusion)
                with self.assertRaisesRegex(recovery.RecoveryError, "did not confirm cancellation"):
                    self.invoke()
                self.dispatch.assert_not_called()

    def test_target_that_finishes_before_cancellation_is_untouched(self):
        self.run_responses = [target_run(), target_run(status="completed", conclusion="success")]
        self.assertEqual(self.invoke(), "untouched")
        self.assert_no_changes()

    def test_assigned_arm_or_active_publisher_is_untouched(self):
        for name, change in (("apple-silicon", {"runner_id": 42}),
                             ("apple-silicon", {"status": "in_progress"}),
                             ("apple-silicon", {"steps": [{"name": "Build"}]}),
                             ("publish", {"status": "in_progress"}),
                             ("publish", {"status": "completed", "conclusion": "success"})):
            with self.subTest(name=name, change=change):
                self.jobs = target_jobs() + [target_job("publish")]
                next(job for job in self.jobs if job["name"] == name).update(change)
                self.assertEqual(self.invoke(), "untouched")
                self.assert_no_changes()

    def test_wrong_initial_target_is_rejected_before_job_lookup_or_cancel(self):
        self.run["head_sha"] = "0" * 40
        with self.assertRaisesRegex(recovery.RecoveryError, "head_sha"):
            self.invoke()
        self.gh.collection.assert_not_called()
        self.assert_no_changes()

    def test_target_provenance_is_rechecked_immediately_before_cancellation(self):
        self.run_responses = [target_run(), target_run(run_attempt=2)]
        with self.assertRaisesRegex(recovery.RecoveryError, "run_attempt"):
            self.invoke()
        self.assert_no_changes()

    def test_confirmation_from_another_attempt_does_not_authorize_dispatch(self):
        self.after_cancel = lambda: self.run.update(
            status="completed", conclusion="cancelled", run_attempt=2)
        with self.assertRaisesRegex(recovery.RecoveryError, "run_attempt"):
            self.invoke()
        self.dispatch.assert_not_called()

    def test_already_completed_retryable_target_dispatches_without_cancelling(self):
        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self.run = target_run(status="completed", conclusion=conclusion)
                self.dispatch.reset_mock()
                self.gh.collection.reset_mock()
                self.assertEqual(self.invoke(), "dispatched")
                self.assertEqual(self.mutations(), [])
                self.dispatch.assert_called_once_with()
                self.gh.collection.assert_called_once_with(RUNS_PATH, "workflow_runs")

    def test_successful_or_unknown_completed_target_is_not_retried(self):
        for conclusion in ("success", "neutral", "skipped", "action_required", None):
            with self.subTest(conclusion=conclusion):
                self.run = target_run(status="completed", conclusion=conclusion)
                with self.assertRaisesRegex(recovery.RecoveryError, "retryable failure"):
                    self.invoke()
                self.assert_no_changes()

    def test_existing_active_recovery_is_reused_after_target_cancellation(self):
        self.runs = [{"id": 9999, "status": "queued"},
                     {"id": 9998, "status": "completed", "conclusion": "failure"}]
        self.assertEqual(self.invoke(), "existing")
        self.assertEqual(self.mutations(), [call(TARGET_PATH + "/cancel", method="POST")])
        self.dispatch.assert_not_called()

    def test_running_or_unknown_other_run_also_prevents_duplicate_dispatch(self):
        self.run.update(status="completed", conclusion="failure")
        for status in ("in_progress", "waiting", None):
            with self.subTest(status=status):
                self.runs = [{"id": 9999, "status": status}]
                self.assertEqual(self.invoke(), "existing")
                self.assert_no_changes()

    def test_completed_other_runs_do_not_prevent_retry(self):
        self.run.update(status="completed", conclusion="failure")
        self.runs = [{"id": 9999, "status": "completed", "conclusion": "cancelled"}]
        self.assertEqual(self.invoke(), "dispatched")
        self.assertEqual(self.mutations(), [])

    def test_changed_main_commit_or_noncommit_ref_prevents_cancel(self):
        for obj in ({"type": "commit", "sha": "0" * 40},
                    {"type": "tag", "sha": HELPER_SHA}, {}):
            with self.subTest(obj=obj):
                self.main_ref = {"object": obj}
                with self.assertRaisesRegex(recovery.RecoveryError, "Main changed"):
                    self.invoke()
                self.assert_no_changes()

    def test_changed_tag_prevents_cancel(self):
        for obj in ({"type": "commit", "sha": "0" * 40},
                    {"type": "tag", "sha": schedule.SOURCE_SHA}, {}):
            with self.subTest(obj=obj):
                self.tag_ref = {"object": obj}
                with self.assertRaisesRegex(recovery.RecoveryError, "tag differs"):
                    self.invoke()
                self.assert_no_changes()

    def test_draft_complete_or_incomplete_release_on_later_page_prevents_cancel(self):
        releases = [{"tag_name": schedule.TAG, "draft": True}, complete_release(),
                    {"tag_name": schedule.TAG, "draft": False, "assets": []}]
        for release in releases:
            with self.subTest(draft=release["draft"], assets=release.get("assets")):
                self.release_pages = [[{"tag_name": "v0.2.1"}], [release]]
                with self.assertRaises(recovery.RecoveryError):
                    self.invoke()
                self.assert_no_changes()

    def test_malformed_release_inventory_prevents_cancel(self):
        for pages in ([], None, {}, [{"tag_name": "v0.2.1"}], [[], {}]):
            with self.subTest(pages=pages):
                self.release_pages = pages
                with self.assertRaisesRegex(recovery.RecoveryError, "release inventory"):
                    self.invoke()
                self.assert_no_changes()

    def test_wrong_or_disabled_workflow_prevents_cancel(self):
        for changes in ({"path": ".github/workflows/other.yml"}, {"state": "disabled_manually"}):
            with self.subTest(changes=changes):
                self.workflow = {"id": WORKFLOW_ID, "path": restart.WORKFLOW_PATH, "state": "active"}
                self.workflow.update(changes)
                with self.assertRaisesRegex(recovery.RecoveryError, "expected active workflow"):
                    self.invoke()
                self.assert_no_changes()

    def test_deadline_prevents_any_api_access_or_cancel(self):
        self.clock.return_value = restart.DEADLINE
        with self.assertRaisesRegex(recovery.RecoveryError, "deadline"):
            self.invoke()
        self.gh.api.assert_not_called()
        self.gh.collection.assert_not_called()
        self.assert_no_changes()

    def test_deadline_reached_during_cancellation_prevents_dispatch(self):
        self.clock.side_effect = [NOW, restart.DEADLINE]
        with self.assertRaisesRegex(recovery.RecoveryError, "deadline"):
            self.invoke()
        self.dispatch.assert_not_called()

    def test_changed_main_tag_or_release_after_cancellation_prevents_dispatch(self):
        for field in ("main", "tag", "release"):
            with self.subTest(field=field):
                self.run = target_run()
                self.main_ref["object"]["sha"] = HELPER_SHA
                self.tag_ref["object"]["sha"] = schedule.SOURCE_SHA
                self.release_pages = [[]]

                def cancel_and_change_context():
                    self.run.update(status="completed", conclusion="cancelled")
                    if field == "main":
                        self.main_ref["object"]["sha"] = "0" * 40
                    elif field == "tag":
                        self.tag_ref["object"]["sha"] = "0" * 40
                    else:
                        self.release_pages.append([complete_release()])

                self.after_cancel = cancel_and_change_context
                with self.assertRaises(recovery.RecoveryError):
                    self.invoke()
                self.dispatch.assert_not_called()


class EntrypointTests(unittest.TestCase):
    def environment(self):
        return {"GITHUB_REPOSITORY": restart.REPOSITORY,
                "GITHUB_WORKFLOW_REF": f"{restart.REPOSITORY}/{restart.HELPER_PATH}@refs/heads/main",
                "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": HELPER_SHA}

    def test_untrusted_entrypoint_context_is_rejected_before_api_access(self):
        alternatives = {
            "GITHUB_REPOSITORY": "someone/fork",
            "GITHUB_WORKFLOW_REF": f"{restart.REPOSITORY}/{restart.HELPER_PATH}@refs/heads/other",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "bad-sha",
        }
        for field, value in alternatives.items():
            with self.subTest(field=field):
                environment = self.environment()
                environment[field] = value
                with patch.dict(os.environ, environment, clear=True), \
                        patch.object(restart, "GitHub") as github, \
                        patch.object(restart.subprocess, "run") as command:
                    with self.assertRaises(recovery.RecoveryError):
                        restart.main()
                github.assert_not_called()
                command.assert_not_called()

    def test_entrypoint_dispatches_only_fixed_workflow_on_main(self):
        with patch.dict(os.environ, self.environment(), clear=True), \
                patch.object(restart, "GitHub") as github, \
                patch.object(restart, "restart") as guarded_restart, \
                patch.object(restart.subprocess, "run") as command:
            guarded_restart.side_effect = lambda gh, sha, dispatch: dispatch()
            restart.main()
        self.assertEqual(guarded_restart.call_args.args[:2], (github.return_value, HELPER_SHA))
        command.assert_called_once_with(
            ["gh", "workflow", "run", restart.WORKFLOW_FILE,
             "--repo", restart.REPOSITORY, "--ref", "main"], check=True, timeout=60)


if __name__ == "__main__":
    unittest.main()
