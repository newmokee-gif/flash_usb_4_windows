#!/usr/bin/env python3
"""One-off, guarded replacement of a specific unassigned macOS recovery run."""
from datetime import datetime, timezone
import os
import subprocess
import time

from recover_multios import GitHub, REPOSITORY, require
from recovery_schedule import DEADLINE, WORKFLOW_FILE, WORKFLOW_PATH, annotate, decide


TARGET_RUN = 40380872693
TARGET_SHA = "a06bd15e38828934516da067c4660daaf105f1ae"
HELPER_PATH = ".github/workflows/restart-recovery.yml"


def validate_target(run, workflow_id):
    expected = {
        "id": TARGET_RUN, "run_attempt": 1, "head_sha": TARGET_SHA,
        "head_branch": "main", "event": "push", "path": WORKFLOW_PATH,
        "workflow_id": workflow_id,
    }
    for key, value in expected.items():
        require(run.get(key) == value, f"Refusing restart: unexpected {key}: {run.get(key)!r}")
    for key in ("repository", "head_repository"):
        require((run.get(key) or {}).get("full_name") == REPOSITORY,
                f"Refusing restart: unexpected {key}")


def unassigned(job):
    return (job.get("status") == "queued" and job.get("conclusion") is None
            and job.get("runner_id") in (None, 0)
            and not job.get("runner_name") and not job.get("steps"))


def safe_to_cancel(run, jobs):
    if run.get("status") not in {"queued", "in_progress"}:
        return False
    selected = {}
    for job in jobs:
        name = job.get("name")
        require(name in {"control", "preflight", "apple-silicon", "publish"}
                and name not in selected, "Unexpected or duplicate recovery job")
        require(job.get("run_id") == TARGET_RUN and job.get("run_attempt") == 1
                and job.get("head_sha") == TARGET_SHA, "Unexpected recovery job source")
        selected[name] = job
    for name in ("control", "preflight"):
        job = selected.get(name, {})
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            return False
    return (unassigned(selected.get("apple-silicon", {}))
            and ("publish" not in selected or unassigned(selected["publish"])))


def validate_context(gh, helper_sha, now):
    require(now < DEADLINE, "Recovery deadline reached; no new restart permitted")
    workflow = gh.api(f"actions/workflows/{WORKFLOW_FILE}")
    require(workflow.get("path") == WORKFLOW_PATH and workflow.get("state") == "active",
            "Recovery workflow is not the expected active workflow")
    main = gh.api("git/ref/heads/main")
    target = main.get("object") or {}
    require(target.get("type") == "commit" and target.get("sha") == helper_sha,
            "Main changed during restart")
    pages = gh.api("releases?per_page=100", paginate=True)
    require(isinstance(pages, list) and pages and all(isinstance(p, list) for p in pages),
            "Invalid release inventory")
    result = decide(gh.api("git/ref/tags/v0.3.0"), [r for p in pages for r in p], now)
    require(result.proceed and not result.error, result.message)
    return workflow["id"]


def restart(gh, helper_sha, dispatch, sleep=time.sleep,
            clock=lambda: datetime.now(timezone.utc)):
    workflow_id = validate_context(gh, helper_sha, clock())
    target_path = f"actions/runs/{TARGET_RUN}"
    run = gh.api(target_path)
    validate_target(run, workflow_id)
    if run.get("status") == "completed":
        require(run.get("conclusion") in {"failure", "cancelled", "timed_out"},
                "Target already completed without a retryable failure; leaving it untouched")
        annotate("notice", f"Target run {TARGET_RUN} already ended: {run['conclusion']}")
    else:
        # Re-read run and jobs immediately before cancellation, jobs last.
        # GitHub's cancel API has no
        # conditional operation; a runner assignment can still race this check.
        run = gh.api(target_path)
        validate_target(run, workflow_id)
        jobs = gh.collection(target_path + "/attempts/1/jobs?per_page=100", "jobs")
        if not safe_to_cancel(run, jobs):
            annotate("notice", "Target is no longer an unassigned queue entry; leaving useful work untouched")
            return "untouched"
        gh.api(target_path + "/cancel", method="POST")
        for _ in range(25):
            run = gh.api(target_path)
            validate_target(run, workflow_id)
            if run.get("status") == "completed":
                break
            sleep(5)
        require(run.get("status") == "completed" and run.get("conclusion") == "cancelled",
                "Target did not confirm cancellation; refusing to dispatch another run")
        annotate("notice", f"Cancelled unassigned recovery run {TARGET_RUN}")

    validate_context(gh, helper_sha, clock())
    runs = gh.collection(f"actions/workflows/{WORKFLOW_FILE}/runs?branch=main&per_page=100",
                         "workflow_runs")
    active = [r for r in runs if r.get("status") != "completed"]
    if active:
        annotate("notice", "An existing recovery attempt will continue; no duplicate dispatched: "
                 + ", ".join(str(r["id"]) for r in active))
        return "existing"
    dispatch()
    annotate("notice", "Fresh release-recovery.yml dispatch accepted on main; all eight application checks remain required")
    return "dispatched"


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_WORKFLOW_REF") == f"{REPOSITORY}/{HELPER_PATH}@refs/heads/main"
            and os.environ.get("GITHUB_EVENT_NAME") == "push", "Unexpected restart context")
    helper_sha = os.environ.get("GITHUB_SHA", "")
    require(len(helper_sha) == 40 and all(c in "0123456789abcdef" for c in helper_sha),
            "Missing helper commit")
    restart(GitHub(), helper_sha, lambda: subprocess.run(
        ["gh", "workflow", "run", WORKFLOW_FILE, "--repo", REPOSITORY, "--ref", "main"],
        check=True, timeout=60))


if __name__ == "__main__":
    main()
