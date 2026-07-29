"""Repository-level contract tests."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "household_tasks"


class ProjectContractTests(unittest.TestCase):
    def test_versions_are_aligned(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
        constants = (INTEGRATION / "const.py").read_text("utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
        version = manifest["version"]
        semver = re.compile(
            r"^(0|[1-9]\d*)\."
            r"(0|[1-9]\d*)\."
            r"(0|[1-9]\d*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
        )

        self.assertRegex(version, semver)
        self.assertIn(f'INTEGRATION_VERSION = "{version}"', constants)
        self.assertIn(f'version = "{version}"', pyproject)

    def test_hacs_layout_is_self_contained(self):
        manifest = json.loads((INTEGRATION / "manifest.json").read_text("utf-8"))
        hacs = json.loads((ROOT / "hacs.json").read_text("utf-8"))

        self.assertEqual(manifest["domain"], "household_tasks")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(hacs["name"], manifest["name"])
        self.assertTrue(
            (INTEGRATION / "frontend" / "household-tasks-panel.js").is_file()
        )
        self.assertTrue((INTEGRATION / "translations" / "de.json").is_file())
        self.assertTrue((INTEGRATION / "translations" / "en.json").is_file())
        brand = INTEGRATION / "brand"
        self.assertTrue((brand / "icon.png").is_file())
        self.assertTrue((brand / "icon@2x.png").is_file())

    def test_frontend_uses_guided_reference_selectors(self):
        panel = (INTEGRATION / "frontend" / "household-tasks-panel.js").read_text(
            "utf-8"
        )

        for helper in (
            "_entityInput(",
            "_notifyInput(",
            "_userInput(",
            "_deviceInput(",
            "_tagInput(",
            "_bindTagCreator(",
            "_followUpRows(",
            "_triggerRows(",
            "_resourceRows(",
            "_escalationRows(",
            "_bindInlineTaskCreates(",
            'type: "household_tasks/preview_task"',
            'type: "household_tasks/test_notification"',
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, panel)

        self.assertNotIn('name="follow_ups"', panel)
        self.assertNotIn('name="triggers"', panel)
        self.assertIn('name="trigger_entity_id"', panel)
        self.assertIn('name="follow_up_task_id"', panel)
        self.assertIn("_enhanceAccessibility(", panel)
        self.assertIn('setAttribute("aria-describedby"', panel)
        self.assertIn('setAttribute("aria-modal", "true")', panel)
        self.assertIn('setAttribute("aria-live"', panel)
        self.assertIn('event.key === "Escape"', panel)
        self.assertIn("new URL(import.meta.url)", panel)
        self.assertIn("encodeURIComponent(frontendVersion)", panel)
        self.assertIn('type: "tag/create"', panel)
        self.assertIn('type: "tag/write"', panel)
        self.assertIn("data-toggle-tag-creator", panel)
        self.assertNotIn(".weekdays input{display:none}", panel)

        engine = (INTEGRATION / "engine.py").read_text("utf-8")
        self.assertIn("def _frontend_version()", engine)
        self.assertIn("cache_headers=False", engine)

    def test_runtime_and_personal_workspace_files_are_absent(self):
        for relative in (
            "config",
            "mosquitto",
            "docker-compose.yml",
            ".env",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / relative).exists())

        scanned = [
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "pyproject.toml",
            *INTEGRATION.rglob("*.py"),
            *(ROOT / "tests").rglob("*.py"),
        ]
        for path in scanned:
            content = path.read_text("utf-8")
            with self.subTest(path=path):
                self.assertNotIn("C:\\Users\\", content)
                private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
                self.assertNotIn(private_key_marker, content)

    def test_json_files_are_valid(self):
        files = [
            ROOT / "hacs.json",
            INTEGRATION / "manifest.json",
            INTEGRATION / "strings.json",
            INTEGRATION / "translations" / "de.json",
            INTEGRATION / "translations" / "en.json",
        ]
        for path in files:
            with self.subTest(path=path):
                json.loads(path.read_text("utf-8"))

    def test_workflow_actions_are_pinned_to_commit_shas(self):
        action_pattern = re.compile(
            r"^\s*(?:-\s+)?uses:\s+([^@\s]+)@([0-9a-f]{40})", re.MULTILINE
        )
        for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
            content = workflow.read_text("utf-8")
            uses_lines = [
                line
                for line in content.splitlines()
                if re.match(r"^\s*(?:-\s+)?uses:", line)
            ]
            matches = action_pattern.findall(content)
            with self.subTest(workflow=workflow):
                self.assertTrue(uses_lines)
                self.assertEqual(len(matches), len(uses_lines))

    def test_enterprise_quality_workflows_exist(self):
        expected = {
            "release.yml",
            "scorecard.yml",
            "security.yml",
            "sonarqube.yml",
            "tests.yml",
            "validate.yml",
        }
        actual = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}
        self.assertEqual(actual, expected)

    def test_release_automation_is_secretless_and_synchronizes_versions(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

        self.assertIn("scripts/prepare_release.py", workflow)
        self.assertIn("refs/tags/$RELEASE_TAG", workflow)
        self.assertIn("github.token", workflow)
        self.assertIn("commits/$MERGE_SHA/pulls", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertNotIn("RELEASE_TOKEN", workflow)
        self.assertNotIn("git push origin HEAD:main", workflow)
        self.assertNotIn("ref: main", workflow)
        self.assertTrue((ROOT / "scripts" / "prepare_release.py").is_file())


if __name__ == "__main__":
    unittest.main()
