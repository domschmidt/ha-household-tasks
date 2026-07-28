"""Tests for deterministic per-pull-request release planning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.prepare_release as release
from scripts.prepare_release import calculate_version, update_release_files


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("fix: repair import", "", "3.2.5"),
        ("docs: explain NFC tags", "", "3.2.5"),
        ("feat: add task groups", "", "3.3.0"),
        ("feat!: replace export schema", "", "4.0.0"),
        ("fix: migrate storage", "BREAKING CHANGE: new schema", "4.0.0"),
    ],
)
def test_calculate_version(title, body, expected):
    assert (
        calculate_version("3.0.0", ["v3.2.4", "not-a-version"], title, body) == expected
    )


def test_invalid_title_is_rejected():
    with pytest.raises(ValueError, match="Conventional Commit"):
        calculate_version("3.0.0", [], "an arbitrary title", "")


def test_update_release_files(tmp_path: Path):
    integration = tmp_path / "custom_components" / "household_tasks"
    integration.mkdir(parents=True)
    (integration / "manifest.json").write_text(
        '{"domain": "household_tasks",\n  "version": "3.0.0"\n}\n',
        "utf-8",
    )
    (integration / "const.py").write_text(
        'INTEGRATION_VERSION = "3.0.0"\n',
        "utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "3.0.0"\n',
        "utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\nDescription.\n\n## 3.0.0 - 2026-01-01\n",
        "utf-8",
    )

    update_release_files(
        tmp_path,
        "3.1.0",
        "feat: add groups",
        42,
        "example/project",
        "2026-07-28",
    )

    manifest = json.loads((integration / "manifest.json").read_text("utf-8"))
    assert manifest["version"] == "3.1.0"
    assert 'INTEGRATION_VERSION = "3.1.0"' in (integration / "const.py").read_text(
        "utf-8"
    )
    assert 'version = "3.1.0"' in (tmp_path / "pyproject.toml").read_text("utf-8")
    changelog = (tmp_path / "CHANGELOG.md").read_text("utf-8")
    assert changelog.index("## 3.1.0") < changelog.index("## 3.0.0")
    assert "[#42](https://github.com/example/project/pull/42)" in changelog


def test_prepare_inherits_previous_tag_changelog(tmp_path: Path, monkeypatch):
    integration = tmp_path / "custom_components" / "household_tasks"
    integration.mkdir(parents=True)
    (integration / "manifest.json").write_text(
        '{"domain": "household_tasks",\n  "version": "3.0.0"\n}\n',
        "utf-8",
    )
    (integration / "const.py").write_text(
        'INTEGRATION_VERSION = "3.0.0"\n',
        "utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "3.0.0"\n',
        "utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\nDevelopment baseline.\n\n## 3.0.0 - 2026-01-01\n",
        "utf-8",
    )
    previous_changelog = (
        "# Changelog\n\nDevelopment baseline.\n\n"
        "## 3.1.0 - 2026-07-27\n\n### Added\n\n- Previous release.\n"
    )
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "INTEGRATION", integration)
    monkeypatch.setattr(
        release,
        "_tags_and_messages",
        lambda: {"v3.1.0": "household-tasks-pr:41"},
    )
    monkeypatch.setattr(release, "_git", lambda *args: previous_changelog.strip())

    plan = release.prepare(
        42,
        "fix: repair imports",
        "",
        "example/project",
        "2026-07-28",
    )

    assert plan.version == "3.1.1"
    changelog = (tmp_path / "CHANGELOG.md").read_text("utf-8")
    assert changelog.index("## 3.1.1") < changelog.index("## 3.1.0")
    assert "Previous release." in changelog


def test_prepare_reuses_tag_marked_for_pull_request(tmp_path: Path, monkeypatch):
    integration = tmp_path / "custom_components" / "household_tasks"
    integration.mkdir(parents=True)
    (integration / "manifest.json").write_text(
        '{"domain": "household_tasks",\n  "version": "3.0.0"\n}\n',
        "utf-8",
    )
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "INTEGRATION", integration)
    monkeypatch.setattr(
        release,
        "_tags_and_messages",
        lambda: {"v3.1.0": "Household Tasks\nhousehold-tasks-pr:42"},
    )

    plan = release.prepare(
        42,
        "feat: add groups",
        "",
        "example/project",
        "2026-07-28",
    )

    assert plan == release.ReleasePlan("3.1.0", "v3.1.0", True)
    assert (
        json.loads((integration / "manifest.json").read_text("utf-8"))["version"]
        == "3.0.0"
    )
