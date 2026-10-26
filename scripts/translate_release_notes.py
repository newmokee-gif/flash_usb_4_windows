"""Update documented release titles/bodies only; never touch assets or visibility."""
from pathlib import Path
import json
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
        name = f"WinUSB Creator {tag[1:]} — Windows, Linux and macOS"
        body = (root / "docs/releases" / f"{tag}.md").read_text(encoding="utf-8")
        matches = [r for r in releases if r["tag_name"] == tag]
        if not matches:
            # GitHub clears a draft's tag when PATCH omits tag_name. Recover only
            # our uniquely identifiable translated drafts, never arbitrary releases.
            matches = [r for r in releases if r["tag_name"].startswith("untagged-")
                       and r["draft"] and r.get("name") == name and r.get("body") == body]
        require(len(matches) <= 1, f"Duplicate release: {tag}")
        if not matches:
            continue
        require(body.strip() and not re.search(r"[\u0400-\u04ff]", body),
                f"Missing English notes: {tag}")
        plans.append((matches[0], tag, name, body))
    require(plans, "No documented releases found")
    for original, tag, name, body in plans:
        current = gh.release(original["id"])
        require(identity(current) == identity(original), "Release changed before translation")
        ref = gh.api(f"git/ref/tags/{tag}")
        require(ref.get("ref") == f"refs/tags/{tag}", "Original tag is missing")
        gh.edit(current["id"], name=name, body=body, tag_name=tag,
                draft=current["draft"], prerelease=current["prerelease"])
        updated = gh.release(current["id"])
        require(updated["name"] == name and updated["body"] == body,
                "Release translation was not saved")
        before, after = identity(current), identity(updated)
        before["tag_name"] = tag
        changed = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
        require(not changed, f"Non-text release fields changed for {current['tag_name']}: {changed}")
        print(f"::notice::English release notes verified: {tag}")
    for release in releases:
        if release["id"] not in {plan[0]["id"] for plan in plans}:
            metadata = json.dumps({key: release.get(key) for key in ("tag_name", "name", "body")}, ensure_ascii=False)
            print("::notice::Historical notes for translation: " + metadata.replace("%", "%25"))


if __name__ == "__main__":
    try:
        translate(GitHub(), Path(__file__).resolve().parents[1])
    except subprocess.CalledProcessError as exc:
        # gh captures API errors; surface the response without printing credentials.
        raise RuntimeError(f"Release metadata API failed ({exc.cmd[2]}): {exc.stderr}") from None
