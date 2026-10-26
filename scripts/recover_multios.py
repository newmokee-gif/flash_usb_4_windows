#!/usr/bin/env python3
"""Recover v0.3.0 from its checked builds and a native Apple Silicon rebuild.

Run from the exact v0.3.0 source checkout. This helper lives in a separate
checkout containing the recovery workflow. --audit-only performs no writes to
GitHub and needs no downloaded application artifacts.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile


REPOSITORY = "newmokee-gif/flash_usb_4_windows"
TAG = "v0.3.0"
SOURCE_SHA = "6809f2cc4c816ff46f01078b0e784f8225ff0813"
SOURCE_RUN = 40376220121
SOURCE_ATTEMPT = 1
WORKFLOW = ".github/workflows/desktop-release.yml"
REQUIRED_JOBS = {
    "catalog", "linux / Debian", "linux / Fedora", "linux / Arch",
    "packages / Build and install deb", "packages / Build and install rpm",
    "appimage / build", "appimage / AppImage on Debian 12",
    "appimage / AppImage on Fedora", "appimage / AppImage on Arch",
    "linux-arm / build", "linux-arm / Validate Debian 12 ARM64",
    "linux-arm / Validate Fedora ARM64", "macos / Native macOS Intel",
    "windows (windows-11-arm, ARM64, arm64)",
    "windows (windows-2025, x64, x64)",
}
ARTIFACT_FILES = {
    "desktop-windows-x64": {
        "WinUSB-Creator-Windows-x64.exe", "SHA256SUMS-Windows-x64.txt"},
    "desktop-windows-ARM64": {
        "WinUSB-Creator-Windows-ARM64.exe", "SHA256SUMS-Windows-ARM64.txt"},
    "macos-Intel": {
        "WinUSB-Creator-macOS-Intel.dmg", "SHA256SUMS-macOS-Intel.txt"},
    "native-deb": {"flash-usb_0.3.0_all.deb"},
    "native-rpm": {"flash-usb-0.3.0-1.noarch.rpm"},
    "appimage": {"flash-usb-v0.3.0-x86_64.AppImage"},
    "linux-arm64-appimage": {"flash-usb-v0.3.0-aarch64.AppImage"},
}
ARM_FILES = {
    "WinUSB-Creator-macOS-AppleSilicon.dmg", "SHA256SUMS-macOS-AppleSilicon.txt"}


class RecoveryError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise RecoveryError(message)


def file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class GitHub:
    def api(self, suffix, method="GET", paginate=False, timeout=60):
        command = ["gh", "api", f"repos/{REPOSITORY}/{suffix}", "--method", method]
        if paginate:
            command.extend(["--paginate", "--slurp"])
        result = subprocess.run(command, text=True, capture_output=True,
                                check=True, timeout=timeout)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def collection(self, suffix, key):
        pages = self.api(suffix, paginate=True)
        require(isinstance(pages, list) and pages, "Missing API collection pages")
        return [item for page in pages for item in page[key]]

    def download_archive(self, artifact_id, destination):
        with destination.open("xb") as stream:
            subprocess.run([
                "gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip",
                "--method", "GET"], stdout=stream, check=True, timeout=600)


def validate_run(run):
    require(run.get("id") == SOURCE_RUN, f"Wrong original workflow run: {run.get('id')!r}")
    require(run.get("run_attempt") == SOURCE_ATTEMPT,
            f"Original run was rerun: run_attempt={run.get('run_attempt')!r}")
    require(run.get("path") == WORKFLOW, f"Wrong original workflow path: {run.get('path')!r}")
    require(run.get("event") == "push", f"Original run was not a push: {run.get('event')!r}")
    require(run.get("head_branch") == TAG,
            f"Original run was not for the release tag: head_branch={run.get('head_branch')!r}")
    require(run.get("head_sha") == SOURCE_SHA,
            f"Original run source SHA differs: {run.get('head_sha')!r}")
    for field in ("repository", "head_repository"):
        require(run.get(field, {}).get("full_name") == REPOSITORY,
                f"Wrong original {field}: {run.get(field, {}).get('full_name')!r}")
    require(run.get("status") in {"queued", "in_progress", "completed"},
            f"Unexpected original run status: {run.get('status')!r}")


def validate_jobs(jobs):
    selected = {}
    for job in jobs:
        name = job.get("name")
        if name not in REQUIRED_JOBS:
            continue
        require(name not in selected, f"Duplicate required job: {name}")
        require(job.get("run_id") == SOURCE_RUN and job.get("head_sha") == SOURCE_SHA,
                f"Wrong source for job: {name}: run_id={job.get('run_id')!r}, "
                f"head_sha={job.get('head_sha')!r}")
        require(job.get("run_attempt") == SOURCE_ATTEMPT,
                f"Wrong attempt for job: {name}: {job.get('run_attempt')!r}")
        require(job.get("status") == "completed" and job.get("conclusion") == "success",
                f"Required job did not succeed: {name}: "
                f"status={job.get('status')!r}, conclusion={job.get('conclusion')!r}")
        selected[name] = job
    require(set(selected) == REQUIRED_JOBS,
            f"Missing required jobs: {sorted(REQUIRED_JOBS - set(selected))}; "
            f"received names: {[job.get('name') for job in jobs]}")
    return selected


def validate_artifacts(artifacts):
    selected = {}
    ids = set()
    for artifact in artifacts:
        name = artifact.get("name")
        if name not in ARTIFACT_FILES:
            continue
        require(name not in selected, f"Duplicate artifact: {name}")
        artifact_id = artifact.get("id")
        require(type(artifact_id) is int and artifact_id > 0 and artifact_id not in ids,
                f"Invalid artifact ID: {name}: {artifact_id!r}")
        require(artifact.get("expired") is False,
                f"Expired artifact: {name}: expired={artifact.get('expired')!r}")
        require(type(artifact.get("size_in_bytes")) is int and artifact["size_in_bytes"] > 0,
                f"Empty artifact: {name}: size_in_bytes={artifact.get('size_in_bytes')!r}")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest") or "") is not None,
                f"Missing trustworthy SHA256 archive digest: {name}: {artifact.get('digest')!r}")
        source = artifact.get("workflow_run") or {}
        require(source.get("id") == SOURCE_RUN and source.get("head_sha") == SOURCE_SHA
                and source.get("head_branch") == TAG, f"Wrong artifact source: {name}: {source!r}")
        selected[name] = artifact
        ids.add(artifact_id)
    require(set(selected) == set(ARTIFACT_FILES),
            f"Missing artifacts: {sorted(set(ARTIFACT_FILES) - set(selected))}")
    return selected


def extract_archive(archive, artifact, destination):
    name = artifact["name"]
    require("sha256:" + file_sha256(archive) == artifact["digest"],
            f"Artifact archive digest mismatch: {name}")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        names = [entry.filename for entry in entries]
        require(len(names) == len(set(names)) and set(names) == ARTIFACT_FILES[name],
                f"Unexpected archive file names: {name}: {names}")
        for entry in entries:
            kind = stat.S_IFMT(entry.external_attr >> 16)
            require(not entry.is_dir() and kind in (0, stat.S_IFREG) and entry.file_size > 0,
                    f"Unsafe or empty archive entry: {entry.filename}")
            output = destination / entry.filename
            require(not output.exists() and not output.is_symlink(),
                    f"Refusing to overwrite artifact: {entry.filename}")
        # Names are exact flat allowlisted filenames; never extract arbitrary paths.
        for entry in entries:
            with package.open(entry) as source, (destination / entry.filename).open("xb") as output:
                shutil.copyfileobj(source, output)


def source_jobs(gh):
    return gh.collection(f"actions/runs/{SOURCE_RUN}/jobs?filter=latest&per_page=100", "jobs")


def verify_tag(gh):
    ref = gh.api(f"git/ref/tags/{TAG}")
    require(ref.get("object", {}).get("type") == "commit"
            and ref["object"].get("sha") == SOURCE_SHA,
            f"Release tag changed: {ref.get('object')!r}")
    return ref


def verify_destination_absent(gh):
    pages = gh.api("releases?per_page=100", paginate=True)
    releases = [release for page in pages for release in page]
    require(not any(release.get("tag_name") == TAG for release in releases),
            "Release v0.3.0 already exists; inspect it without overwriting or deleting")


def require_publisher_unstarted(jobs):
    publishers = [job for job in jobs if job.get("name") == "publish"]
    require(len(publishers) <= 1, "Ambiguous original publish jobs")
    for job in publishers:
        skipped = job.get("status") == "completed" and job.get("conclusion") == "skipped"
        # GitHub can populate started_at with queue creation time before a
        # runner is acquired, so that timestamp alone does not indicate execution.
        queued = job.get("status") in {"queued", "waiting", "pending"}
        cancelled_before_execution = (job.get("status") == "completed"
                                      and job.get("conclusion") == "cancelled"
                                      and not job.get("steps") and not job.get("runner_id"))
        require(skipped or queued or cancelled_before_execution,
                "Original publisher has started; inspect its result without cancelling it: "
                f"status={job.get('status')!r}, conclusion={job.get('conclusion')!r}, "
                f"runner_id={job.get('runner_id')!r}")


def stop_original_run(gh, audit):
    run = gh.api(f"actions/runs/{SOURCE_RUN}")
    audit["source_run_before_stop"] = run
    validate_run(run)
    jobs = source_jobs(gh)
    audit["source_jobs_before_stop"] = jobs
    validate_jobs(jobs)
    require_publisher_unstarted(jobs)
    verify_destination_absent(gh)
    if run["status"] in {"queued", "in_progress"}:
        gh.api(f"actions/runs/{SOURCE_RUN}/cancel", method="POST")
        audit["original_cancellation_requested"] = True
        deadline = time.monotonic() + 120
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "Original run did not stop within 120 seconds")
            run = gh.api(f"actions/runs/{SOURCE_RUN}", timeout=min(30, remaining))
            audit["source_run_after_stop"] = run
            validate_run(run)
            if run["status"] == "completed":
                break
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    audit["source_run_after_stop"] = run
    jobs = source_jobs(gh)
    audit["source_jobs_after_stop"] = jobs
    validate_jobs(jobs)
    require_publisher_unstarted(jobs)
    verify_destination_absent(gh)
    verify_tag(gh)


def recover(gh, publisher, root, audit, audit_only=False):
    audit["verified_tag"] = verify_tag(gh)
    run = gh.api(f"actions/runs/{SOURCE_RUN}")
    audit["source_run"] = run
    validate_run(run)
    jobs = source_jobs(gh)
    audit["source_jobs"] = jobs
    validate_jobs(jobs)
    require_publisher_unstarted(jobs)
    raw_artifacts = gh.collection(f"actions/runs/{SOURCE_RUN}/artifacts?per_page=100", "artifacts")
    audit["source_artifacts_received"] = raw_artifacts
    artifacts = validate_artifacts(raw_artifacts)
    audit["source_artifacts"] = artifacts
    verify_destination_absent(gh)
    if audit_only:
        audit["outcome"] = "metadata verified; no GitHub mutations"
        return
    builds = root / "desktop-dist"
    require(builds.is_dir() and not builds.is_symlink(), "Unsafe artifact directory")
    require({path.name for path in builds.iterdir()} == ARM_FILES,
            "Expected only the two newly checked Apple Silicon files before recovery")
    for path in builds.iterdir():
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                f"Unsafe Apple Silicon artifact: {path.name}")
    with tempfile.TemporaryDirectory(prefix="winusb-recovery-") as temporary:
        for name, artifact in sorted(artifacts.items()):
            archive = Path(temporary) / f"{artifact['id']}.zip"
            gh.download_archive(artifact["id"], archive)
            extract_archive(archive, artifact, builds)
    audit["verified_files"] = publisher.validate_inputs(builds)
    stop_original_run(gh, audit)
    audit["outcome"] = "all inputs verified; invoking original publisher"
    (root / "recovery-audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    # Only the original, independently verified source publisher sees this tag
    # context. GITHUB_RUN_ID remains the actual recovery run for its provenance.
    previous = {name: os.environ.get(name) for name in ("GITHUB_SHA", "GITHUB_REF_NAME")}
    try:
        os.environ.update(GITHUB_SHA=SOURCE_SHA, GITHUB_REF_NAME=TAG)
        publisher.main()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    audit["outcome"] = "published and downloaded release verified"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    audit = {"source_commit": SOURCE_SHA, "tag": TAG,
             "recovery_workflow_run": os.environ.get("GITHUB_RUN_ID"),
             "recovery_workflow_commit": os.environ.get("GITHUB_SHA")}
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        require(commit == SOURCE_SHA, "Run recovery from the exact release source checkout")
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong recovery repository")
        sys.path.insert(0, str(root / "scripts"))
        publisher = importlib.import_module("publish_multios")
        require(Path(publisher.__file__).resolve() == root / "scripts/publish_multios.py",
                "Publisher was not loaded from the release source checkout")
        recover(GitHub(), publisher, root, audit, args.audit_only)
    except Exception as error:
        audit["outcome"] = "failed"
        audit["error"] = str(error)
        raise
    finally:
        (root / "recovery-audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
