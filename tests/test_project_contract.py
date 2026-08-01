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
            "_showSetupWizard(",
            "_showCommandPalette(",
            "_showGallery(",
            "_showWhyNot(",
            "_renderMobileQuickActions(",
            "_renderMine(",
            "_renderWeek(",
            "_renderHistory(",
            "_historyEventDetails(",
            "_openAttachment(",
            "has-evidence",
            "no-evidence",
            'data-task-history="',
            "HT_PANEL_VIEWS",
            "_viewFromLocation(",
            "_navigateToView(",
            "_viewUrl(",
            'addEventListener("popstate"',
            'addEventListener("location-changed"',
            "HT_PANEL_VIEW_URLS",
            'mine: "/haushaltsaufgaben?view=mine"',
            "week_preview",
            'class="week-preview"',
            "_bindBulkActions(",
            "_showDiscoveryInstall(",
            "_showTodayPlanner(",
            "_showBatchCapture(",
            "_showNaturalMove(",
            "_showAttachments(",
            "_showDeviceFile(",
            "_showTaskStackEditor(",
            "_bindWeekDragDrop(",
            "_weatherRows(",
            "_bindWeatherEditor(",
            'type: "household_tasks/preview_task"',
            'type: "household_tasks/test_notification"',
            'type: "household_tasks/smart_task_preview"',
            'this._call("undo")',
            'this._call("request_help"',
            'this._call("snooze"',
            'this._call("bulk"',
            'this._call("toggle_favorite"',
            'this._call("install_discovery"',
            'this._call("move_occurrence"',
            'this._call("create_batch"',
            'type: "household_tasks/add_attachment_chunk"',
            'type: "household_tasks/attachment_content_chunk"',
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
        self.assertIn('Object.hasOwn(this, "hass")', panel)
        self.assertIn("event.stopImmediatePropagation()", panel)
        self.assertIn("_setInlinePanelOpen(", panel)
        self.assertIn("control.disabled = !open", panel)
        self.assertIn('panel.toggleAttribute("inert", !open)', panel)
        self.assertIn("new URL(import.meta.url)", panel)
        self.assertIn("encodeURIComponent(frontendVersion)", panel)
        self.assertIn('type: "tag/create"', panel)
        self.assertIn('type: "tag/write"', panel)
        self.assertIn("data-toggle-tag-creator", panel)
        self.assertNotIn(".weekdays input{display:none}", panel)

        engine = (INTEGRATION / "engine.py").read_text("utf-8")
        self.assertIn("def _frontend_version()", engine)
        self.assertIn("cache_headers=False", engine)
        for capability in (
            "async_set_household_mode",
            "async_install_gallery_template",
            "async_undo_last",
            "configuration_health",
            "explain_task",
            "async_request_help",
            "preview_smart_task",
            "async_bulk_occurrences",
            "async_toggle_favorite",
            "async_install_discovery_suggestion",
            "_process_notification_digest",
            "async_move_occurrence",
            "async_create_batch",
            "async_save_task_stack",
            "async_add_attachment",
            "_scan_weather_tasks",
            "_weather_decision",
        ):
            with self.subTest(capability=capability):
                self.assertIn(capability, engine)

        guide = (ROOT / "docs" / "user-guide.md").read_text("utf-8")
        for heading in (
            "Einrichtungsassistent und Vorlagengalerie",
            "Urlaub, Gäste und saisonale Aufgaben",
            "Aufgabenmarkt und gegenseitige Hilfe",
            "Suche, Erklärungen und Rückgängig",
            "Gesundheitscheck und Mobilansicht",
            "Persönlicher Arbeitsbereich und Wochenplanung",
            "Smarte Schnellerfassung",
            "Autodiscovery und aktionsfähige Diagnose",
            "Gebündelte Benachrichtigungen",
            "Home Assistant Assist",
            "Komfortplanung und lokale Assistenz",
            "Gewohnheiten, Stapel und flexible Serien",
            "Kontextmenüs, Geräteakten und Anhänge",
            "Fehlervermeidung und Offline-Bedienung",
            "Wetter- und Klimaregeln",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, guide)

        self.assertTrue((INTEGRATION / "intents.py").is_file())
        self.assertTrue((INTEGRATION / "productivity.py").is_file())
        self.assertTrue((INTEGRATION / "weather_rules.py").is_file())
        self.assertTrue((INTEGRATION / "forecast_rules.py").is_file())
        self.assertTrue(
            (
                ROOT / "examples" / "custom_sentences" / "de" / "household_tasks.yaml"
            ).is_file()
        )

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
