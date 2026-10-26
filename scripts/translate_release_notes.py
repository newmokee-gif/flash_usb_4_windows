"""Update documented release titles/bodies only; never touch assets or visibility."""
from pathlib import Path
import re
import subprocess
from consolidate_release import GitHub, require


TAGS = ("v0.2.0", "v0.2.1", "v0.3.0", "v0.3.1", "v0.3.2")


def identity(release):
    result = {key: release.get(key) for key in
              ("id", "tag_name", "target_commitish", "draft", "prerelease")}
    # Download counters change while users fetch files; they are not mutations.
    result["assets"] = sorted(
        ({key: asset.get(key) for key in ("id", "name", "size", "digest", "state")}
         for asset in release.get("assets", [])), key=lambda asset: asset["id"])
    return result


def translate(gh, root):
    releases = gh.list_releases()
    plans = []
    for tag in TAGS:
        matches = [r for r in releases if r["tag_name"] == tag]
        require(len(matches) <= 1, f"Duplicate release: {tag}")
        if not matches:
            continue
        body = (root / "docs/releases" / f"{tag}.md").read_text(encoding="utf-8")
        require(body.strip() and not re.search(r"[\u0400-\u04ff]", body),
                f"Missing English notes: {tag}")
        plans.append((matches[0], f"WinUSB Creator {tag[1:]} — Windows, Linux and macOS", body))
    require(plans, "No documented releases found")
    for original, name, body in plans:
        current = gh.release(original["id"])
        require(identity(current) == identity(original), "Release changed before translation")
        gh.edit(current["id"], name=name, body=body)
        updated = gh.release(current["id"])
        require(updated["name"] == name and updated["body"] == body,
                "Release translation was not saved")
        require(identity(updated) == identity(current), "Non-text release fields changed")
        print(f"::notice::English release notes verified: {current['tag_name']}")
    for release in releases:
        if release["tag_name"] not in TAGS:
            print(f"::notice::Unmapped historical release retained unchanged: {release['tag_name']}")


if __name__ == "__main__":
    try:
        translate(GitHub(), Path(__file__).resolve().parents[1])
    except subprocess.CalledProcessError as exc:
        # gh captures API errors; surface the response without printing credentials.
        raise RuntimeError(f"Release metadata API failed: {exc.stderr}") from None
