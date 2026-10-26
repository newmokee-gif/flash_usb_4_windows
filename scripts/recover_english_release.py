"""Publish v0.3.3 only from its complete, successful native build checks."""
import os
from pathlib import Path
import subprocess
import tempfile
import recover_multios as checked
import publish_multios as publisher

TAG = "v0.3.3"
SHA = "c4705c846dd770481785a36056174c8f79b94bf9"
RUN = 40410611011


def configure():
    checked.TAG, checked.SOURCE_SHA, checked.SOURCE_RUN = TAG, SHA, RUN
    checked.REQUIRED_JOBS = checked.REQUIRED_JOBS | {"macos / Native macOS AppleSilicon"}
    checked.ARTIFACT_FILES = {
        name: {file.replace("0.3.0", "0.3.3") for file in files}
        for name, files in checked.ARTIFACT_FILES.items()}
    checked.ARTIFACT_FILES["macos-AppleSilicon"] = checked.ARM_FILES


def main():
    configure()
    root = Path.cwd()
    checked.require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == SHA,
                    "Recovery requires the exact release source checkout")
    checked.require(os.environ.get("GITHUB_REPOSITORY") == checked.REPOSITORY, "Wrong repository")
    gh = checked.GitHub()
    checked.verify_tag(gh)
    run = gh.api(f"actions/runs/{RUN}")
    checked.validate_run(run)
    checked.require(run["status"] == "completed", "Build run is still active")
    checked.validate_jobs(checked.source_jobs(gh))
    checked.verify_destination_absent(gh)
    artifacts = checked.validate_artifacts(gh.collection(f"actions/runs/{RUN}/artifacts?per_page=100", "artifacts"))
    print("::notice::All 17 native checks and 8 build artifact archives verified", flush=True)
    builds = root / "desktop-dist"
    builds.mkdir()
    with tempfile.TemporaryDirectory(prefix="english-release-") as temporary:
        for artifact in artifacts.values():
            archive = Path(temporary) / f"{artifact['id']}.zip"
            gh.download_archive(artifact["id"], archive)
            checked.extract_archive(archive, artifact, builds)
    publisher.validate_inputs(builds)
    checked.verify_tag(gh)
    checked.verify_destination_absent(gh)
    os.environ.update(GITHUB_SHA=SHA, GITHUB_REF_NAME=TAG, VERIFIED_BUILD_RUN=str(RUN))
    publisher.main()
    print("::notice::Published v0.3.3: all eight applications re-downloaded and checksum-verified")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"GitHub operation failed: {exc.cmd}; {exc.stderr}") from None
