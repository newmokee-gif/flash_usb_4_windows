#!/usr/bin/env python3
"""Bound hourly recovery attempts; never create, change or remove a release."""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path

from recover_multios import ARTIFACT_FILES, ARM_FILES, GitHub, REPOSITORY, SOURCE_SHA, TAG


WORKFLOW_FILE = "release-recovery.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW_FILE
DEADLINE = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
EXPECTED_APPLICATIONS = {
    name for names in (*ARTIFACT_FILES.values(), ARM_FILES) for name in names
    if not name.startswith("SHA256SUMS-")
}


class ScheduleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    proceed: bool
    disable: bool
    message: str
    error: bool = False


def decide(ref, releases, now):
    """Classify verified API data without changing any external state."""
    if now.tzinfo is None:
        raise ScheduleError("Scheduler requires a timezone-aware current time")
    target = ref.get("object") or {}
    if target.get("type") != "commit" or target.get("sha") != SOURCE_SHA:
        return Decision(False, True,
                        f"Release tag differs from pinned source: {target!r}; manual attention required", True)
    matching = [release for release in releases if release.get("tag_name") == TAG]
    if len(matching) > 1:
        return Decision(False, True, "Ambiguous v0.3.0 release inventory; manual attention required", True)
    if matching and matching[0].get("draft") is False:
        assets = matching[0].get("assets") or []
        invalid = []
        for name in sorted(EXPECTED_APPLICATIONS):
            copies = [asset for asset in assets if asset.get("name") == name]
            if (len(copies) != 1 or copies[0].get("state") != "uploaded"
                    or type(copies[0].get("size")) is not int or copies[0]["size"] <= 0):
                invalid.append(name)
        if invalid:
            return Decision(False, True,
                            f"Published v0.3.0 has missing or invalid applications: {invalid}; "
                            "manual attention required", True)
        return Decision(False, True,
                        "All eight v0.3.0 applications are published from the pinned tag; stopping retries")
    if matching and matching[0].get("draft") is not True:
        return Decision(False, True, "Invalid release draft state; manual attention required", True)
    if now >= DEADLINE:
        return Decision(False, True,
                        "Recovery deadline 2026-10-28 00:00 UTC reached without all eight published "
                        "applications; manual attention required", True)
    if matching:
        return Decision(False, False,
                        "An existing v0.3.0 draft requires attention; preserving it and waiting for the next check")
    return Decision(True, False, "No v0.3.0 release exists; continuing complete eight-application recovery")


def annotate(level, message):
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}", flush=True)


def control(gh, now, output):
    # Default to stopping downstream jobs even if an API request fails.
    with output.open("a", encoding="utf-8") as stream:
        stream.write("proceed=false\n")
    ref = gh.api(f"git/ref/tags/{TAG}")
    pages = gh.api("releases?per_page=100", paginate=True)
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ScheduleError("Release inventory response is incomplete; refusing to assume no release")
    result = decide(ref, [release for page in pages for release in page], now)
    if result.disable:
        # Fixed repository comes from GitHub.api; never accept a workflow ID/path
        # from an event payload or from release metadata.
        gh.api(f"actions/workflows/{WORKFLOW_FILE}/disable", method="PUT")
    annotate("error" if result.error else "notice", result.message)
    if result.error:
        raise ScheduleError(result.message)
    if result.proceed:
        with output.open("a", encoding="utf-8") as stream:
            stream.write("proceed=true\n")
    return result


def main():
    expected_ref = f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main"
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_WORKFLOW_REF") != expected_ref):
        raise ScheduleError("Schedule control may run only in this repository's main recovery workflow")
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        raise ScheduleError("GITHUB_OUTPUT is required")
    control(GitHub(), datetime.now(timezone.utc), Path(output))


if __name__ == "__main__":
    main()
