"""Plan one immutable release for one merged pull request."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "household_tasks"
TAG_PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TITLE_PATTERN = re.compile(
    r"^(?P<type>build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(?:\([a-z0-9._/-]+\))?(?P<breaking>!)?:\s+.+$"
)
BREAKING_PATTERN = re.compile(r"^BREAKING CHANGES?:\s+\S", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class ReleasePlan:
    """A version and tag allocated to a pull request."""

    version: str
    tag: str
    existing: bool


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Tag {value!r} is not stable SemVer")
    return tuple(int(part) for part in match.groups())


def calculate_version(
    baseline: str,
    tags: list[str],
    title: str,
    body: str,
) -> str:
    """Calculate the next stable version from all repository tags."""
    title_match = TITLE_PATTERN.fullmatch(title)
    if title_match is None:
        raise ValueError(f"Pull request title is not a Conventional Commit: {title!r}")

    versions = [_version_tuple(f"v{baseline}")]
    versions.extend(_version_tuple(tag) for tag in tags if TAG_PATTERN.fullmatch(tag))
    major, minor, patch = max(versions)

    if title_match["breaking"] or BREAKING_PATTERN.search(body):
        return f"{major + 1}.0.0"
    if title_match["type"] == "feat":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    source = path.read_text("utf-8")
    updated, count = re.subn(pattern, replacement, source, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Expected exactly one version field in {path}")
    path.write_text(updated, "utf-8")


def update_release_files(
    root: Path,
    version: str,
    title: str,
    pr_number: int,
    repository: str,
    release_date: str,
) -> None:
    """Synchronize source versions and prepend one changelog entry."""
    integration = root / "custom_components" / "household_tasks"
    _replace_once(
        integration / "manifest.json",
        r'^  "version": "[^"]+"$',
        f'  "version": "{version}"',
    )
    _replace_once(
        integration / "const.py",
        r'^INTEGRATION_VERSION = "[^"]+"$',
        f'INTEGRATION_VERSION = "{version}"',
    )
    _replace_once(
        root / "pyproject.toml",
        r'^version\s*=\s*"[^"]+"$',
        f'version = "{version}"',
    )

    changelog_path = root / "CHANGELOG.md"
    changelog = changelog_path.read_text("utf-8")
    insertion_point = changelog.find("\n## ")
    if insertion_point == -1:
        raise ValueError("CHANGELOG.md has no release section")
    title_match = TITLE_PATTERN.fullmatch(title)
    if title_match is None:
        raise ValueError(f"Pull request title is not a Conventional Commit: {title!r}")
    change_type = title_match["type"]
    section = (
        "Added"
        if change_type == "feat"
        else "Fixed"
        if change_type == "fix"
        else "Changed"
    )
    escaped_title = title.replace("[", r"\[").replace("]", r"\]")
    pull_request_url = f"https://github.com/{repository}/pull/{pr_number}"
    entry = (
        f"\n## {version} - {release_date}\n\n"
        f"### {section}\n\n"
        f"- {escaped_title} ([#{pr_number}]({pull_request_url}))\n"
    )
    changelog_path.write_text(
        f"{changelog[:insertion_point]}{entry}{changelog[insertion_point:]}",
        "utf-8",
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _tags_and_messages() -> dict[str, str]:
    tags = _git("tag", "--list", "v[0-9]*").splitlines()
    return {
        tag: _git("for-each-ref", f"refs/tags/{tag}", "--format=%(contents)")
        for tag in tags
        if TAG_PATTERN.fullmatch(tag)
    }


def _write_release_notes(
    root: Path,
    plan: ReleasePlan,
    title: str,
    pr_number: int,
    repository: str,
) -> None:
    output = root / "dist" / "release-notes.md"
    output.parent.mkdir(exist_ok=True)
    output.write_text(
        f"## {title}\n\n"
        f"Released from [#{pr_number}](https://github.com/{repository}/pull/{pr_number}).\n\n"
        f"<!-- household-tasks-pr:{pr_number} -->\n",
        "utf-8",
    )


def prepare(
    pr_number: int,
    title: str,
    body: str,
    repository: str,
    release_date: str,
) -> ReleasePlan:
    """Reuse an existing PR tag or prepare the next release in the worktree."""
    tags_and_messages = _tags_and_messages()
    marker = f"household-tasks-pr:{pr_number}"
    existing_tags = [
        tag
        for tag, message in tags_and_messages.items()
        if marker in message.splitlines()
    ]
    if existing_tags:
        tag = max(existing_tags, key=_version_tuple)
        plan = ReleasePlan(tag.removeprefix("v"), tag, True)
    else:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
        release_tags = list(tags_and_messages)
        version = calculate_version(
            str(manifest["version"]),
            release_tags,
            title,
            body,
        )
        plan = ReleasePlan(version, f"v{version}", False)
        if release_tags:
            latest_tag = max(release_tags, key=_version_tuple)
            previous_changelog = _git("show", f"{latest_tag}:CHANGELOG.md")
            (ROOT / "CHANGELOG.md").write_text(f"{previous_changelog}\n", "utf-8")
        update_release_files(
            ROOT,
            version,
            title,
            pr_number,
            repository,
            release_date,
        )

    _write_release_notes(ROOT, plan, title, pr_number, repository)
    return plan


def main() -> None:
    """Prepare release files and print shell-safe tag and state fields."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull-request-file", required=True, type=Path)
    args = parser.parse_args()
    pull_request = json.loads(args.pull_request_file.read_text("utf-8"))
    plan = prepare(
        int(pull_request["number"]),
        str(pull_request["title"]),
        str(pull_request.get("body") or ""),
        str(pull_request["base"]["repo"]["full_name"]),
        datetime.now(UTC).date().isoformat(),
    )
    print(f"{plan.tag}\t{str(plan.existing).lower()}")


if __name__ == "__main__":
    main()
