# Household Tasks for Home Assistant

[![HACS validation](https://github.com/domschmidt/ha-household-tasks/actions/workflows/validate.yml/badge.svg)](https://github.com/domschmidt/ha-household-tasks/actions/workflows/validate.yml)
[![Tests](https://github.com/domschmidt/ha-household-tasks/actions/workflows/tests.yml/badge.svg)](https://github.com/domschmidt/ha-household-tasks/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Household Tasks is a local-first Home Assistant custom integration for planning,
assigning, completing, and evaluating recurring household work. Its own
versioned task domain supports lifecycle status, checklists, dependencies,
completion-based recurrence, state rules, weekly reviews, NFC tags, push
escalation, and a multilingual management panel.

> The repository is prepared for community use and HACS validation. Until the
> first tagged GitHub release exists, install from a local checkout or add the
> repository to HACS as a custom repository.

[Deutsche Anleitung](docs/user-guide.md) ·
[Architecture](ARCHITECTURE.md) ·
[Contributing](CONTRIBUTING.md) ·
[Security](SECURITY.md)

## Highlights

- Native, revisioned task occurrences are the single source of truth.
- Structured checklists, lifecycle status, blocking dependencies, and history.
- Aggregate HA sensors for open, due-today, overdue, and blocked work.
- Fixed, rotating, fair, or open assignment with a human-readable explanation.
- Weekly, monthly, yearly, interval, calendar, state, and completion schedules.
- Dependent follow-up tasks, including delayed chains.
- NFC create/complete flows with visible, configurable scan feedback.
- Escalations, snoozing, help requests, takeover, and actionable notifications.
- Visual resource rules and unlimited, structured escalation stages.
- Guided entity selection, inline creation, and side-effect-free rule previews.
- Vacation and guest modes, seasonal rules, and curated household templates.
- Open task marketplace with priorities, points, rewards, and voluntary help.
- Optional automatic credit for routine work when nobody confirms completion within a configurable grace period.
- Global command search, explainable skipped decisions, health checks, and undo.
- Progressive forms and a mobile top-three quick-action view.
- Personal task inbox, seven-day planner with read-only schedule projections,
  bulk actions, and favorites.
- Local smart capture and entity-based setup suggestions.
- Actionable diagnostics, routine notification digests, and Assist intents.
- Context-aware daily planning, natural moves, and drag-and-drop week planning.
- Temporary per-template pauses with automatic reactivation and preserved open work.
- Transparent local habit suggestions, reusable task stacks, and flexible series.
- Task context menus, device records, bounded photo/PDF attachments, and an
  offline action queue.
- Pre-save volume projections plus duplicate and conflict diagnostics.
- Combined weather and climate rules for temperature, rain, wind, humidity,
  UV, and textual weather states, including AND/OR conditions and previews.
- An expanded curated gallery covering frost, heat, storms, rain, ice, snow,
  ventilation, UV protection, pets, guests, and weekly routines.
- Weekly household review and 90-day analytics.
- Import/export with schema validation and an explicit replace workflow.
- German and English panel localization.
- Admin-only configuration and privacy-aware diagnostics.
- No cloud account, external API, telemetry, or YAML configuration required.

## Requirements

- Home Assistant 2024.10.0 or newer
- HACS 2.0 or newer for the recommended installation path
- The Home Assistant mobile app for actionable push notifications
- Optional: Home Assistant tags/NFC, calendar entities, presence entities, or
  monitored device entities

## Installation

### HACS

1. Open HACS and choose **Integrations**.
2. Add `https://github.com/domschmidt/ha-household-tasks` as a custom
   **Integration** repository until it is available in the default catalog.
3. Search for **Household Tasks**, download it, and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**.
5. Select **Household Tasks**. No external task-list entity is required.

### Manual

1. Copy `custom_components/household_tasks` into the Home Assistant
   `/config/custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings → Devices & services**.

## First configuration

After setup, **Tasks** appears in the sidebar. Add people first and map each
person to the appropriate Home Assistant user, presence entity, and mobile
notification service. Then create reusable task definitions.

Only Home Assistant administrators may change people, task definitions, rules,
or imports. Household members explicitly linked to a Home Assistant user may
complete, claim, update, and create work in the panel.

## Core concepts

### Task definitions and native occurrences

A task definition describes assignment, schedule, due time, escalation,
dependencies, NFC behavior, and checklists. Every run creates an independent
native Household Tasks occurrence in Home Assistant's atomic `Store`. Each
occurrence carries a stable ID, optimistic-lock revision, lifecycle status,
timestamps, checklist progress, dependency references, and an immutable bounded
event history. Definitions and progress therefore survive restarts without a
second integration acting as a mirror or source of truth.

Lifecycle status is `open`, `in_progress`, `waiting`, `blocked`, `completed`, or
`cancelled`. A blocked occurrence cannot be completed until all referenced
prerequisites are terminal. Completion can require every checklist item, and
every transition is available in the per-task history.

### Assignment

| Mode | Behavior |
| --- | --- |
| Fixed | Keeps the selected person responsible and applies an explicit absence policy. |
| Rotation | Cycles through eligible people and persists the cursor. |
| Fair | Uses assignment count, then current workload, then configured order. |
| Open | Creates an unassigned item that an eligible person can claim. |
| Per person | Creates one independent occurrence for every selected person. |

The panel exposes **Why was this assigned to me?** using the factors recorded at
assignment time.

Definitions may require presence. Eligible people are then filtered through
their configured `person.*`, `device_tracker.*`, or `binary_sensor.*` state.
For fixed assignments, the default is deliberately safe: the occurrence waits
for its owner instead of silently moving to somebody else. A definition can
instead name an explicit substitute pool, open the work for that pool to claim,
or keep the absent owner assigned. Fair and rotating assignments wait when none
of their configured candidates is home.

```yaml
assignee: alex
assignment:
  type: fixed
  presence_required: true
  absence_policy: fallback
  fallback_people: [sam]
  fallback_strategy: fair
```

Temporary household handovers transfer both open and future work. They can be
scheduled with an end time, carry an audit reason, and never rewrite the
underlying task definitions.

Routine work that is assumed to happen can use automatic completion. A manual
completion during the grace period always credits the actual person. Only if
nobody confirms the task does the configured default person receive the points.
Blocked tasks are never completed automatically.

```yaml
automatic_completion:
  enabled: true
  default_person: alex
  after: "12:00:00"
```

### Scheduling

| Type | Typical use |
| --- | --- |
| Manual | One-click or service-created work |
| Weekly/monthly/yearly | Calendar-based routines |
| Every N months | Quarterly or semiannual maintenance |
| Calendar event | Preparation or follow-up around appointments |
| State change | Dishwasher finished, filter warning, someone arrived |
| After completion | Repeat relative to when work was actually completed |
| Weather forecast | Prepare before the first matching daily or hourly forecast |

State schedules support `for`, due offsets, cooldowns, and duplicate
suppression. Completion schedules include an initial due date to bootstrap the
series.

Calendar schedules can map case-insensitive regular expressions over event
titles to concrete task names. Mapping rows are evaluated in order, and
unmapped events can be ignored so a shared municipal calendar creates household
work only for relevant collections.

### Forecast planning and once-per-season rules

Forecast schedules call Home Assistant's local `weather.get_forecasts` action.
They evaluate an explicit horizon, retain a condition trace for every examined
period, and can make work due a configurable number of calendar days before the
first matching period. Forecast providers remain responsible for availability
and accuracy; Household Tasks does not contact external weather services.

The **Per person** assignment mode combines with `once_per_season` to create one
independent occurrence and seasonal lock for every selected person:

```yaml
name: Check antifreeze in your own car
assignment:
  type: per_person
  people: [alex, sam]
schedule:
  type: forecast_trigger
  forecast_type: daily
  horizon_hours: 48
  lead_days: 1
  time: "18:00:00"
weather:
  conditions:
    - entity_id: weather.home
      attribute: templow
      condition: below
      threshold: 0
season:
  months: [10, 11, 12, 1, 2, 3]
repeat:
  mode: once_per_season
```

The winter key spans the year boundary, so October 2026 through March 2027 is
one season. Handovers may change the effective assignee without changing whose
seasonal responsibility is recorded.

The editor's rule preview is side-effect free. It shows the first matching
forecast period, activation time, condition results, household-mode and season
decisions, every target person, and existing seasonal locks. Optional scenario
values let administrators test hypothetical forecasts. Successful runtime
evaluations retain the same forecast trace, while **Why not?** exposes the most
recent result. Seasonal locks can be reset deliberately and restored with
Undo.

### Dependencies

Definitions can require open occurrences from other definitions to finish.
Completing one definition can also create one or more follow-up definitions
immediately or after a delay. Cycles and missing references are rejected or
reported by the health check. This models workflows such as:

```text
Start washing machine → Hang laundry → Take laundry down
```

### NFC tags

Assign a Home Assistant tag ID to a task definition and choose:

- create or complete;
- create only; or
- complete only.

The feedback mode can notify the scanning person, the assigned person, both, or
no one. Each handled scan emits `household_tasks_nfc_action` for dashboards and
automations. Treat tag IDs as security-sensitive identifiers and do not publish
them in issues.

## Actions

The integration registers actions during Home Assistant setup:

```yaml
action: household_tasks.create
data:
  task_id: laundry
```

```yaml
action: household_tasks.scan_now
```

`household_tasks.create` creates an occurrence from an active definition.
`household_tasks.scan_now` immediately evaluates schedules and escalations.

Native task state can also be controlled from automations:

```yaml
action: household_tasks.set_status
data:
  occurrence_id: 1234abcd
  status: in_progress
  expected_revision: 2
```

`household_tasks.set_checklist_item` accepts `occurrence_id`, `item_id`,
`completed`, and an optional `expected_revision`. Successful writes emit
`household_tasks_updated` with aggregate counts; revision conflicts fail rather
than silently overwriting a newer panel or automation update.

Temporary handovers are also automation-friendly:

```yaml
action: household_tasks.set_handover
data:
  from_person: person_a
  to_person: person_b
  until: "2026-08-14T18:00:00+02:00"
  reason: Vacation
```

Use `household_tasks.clear_handover` with `from_person` to end it early.

Generic resource monitors turn numeric thresholds or state values into tasks.
Typical sources include consumable levels, filter life, batteries, water
storage, salt, toner, and pet-food containers. A recovered sensor can
automatically resolve its occurrence.

## Backup, export, and restore

Use **Settings → Export** in the panel before larger changes. Exports contain
people mappings, task definitions, automation rules, and identifiers, so store
them like a Home Assistant backup.

Import validates the schema before replacing configuration. Take a Home
Assistant backup as well; import is a configuration migration tool, not a
replacement for instance backups.

## Diagnostics and privacy

All processing is local. The integration does not transmit telemetry or
household data. Home Assistant diagnostics redact user, person, device, entity,
tag, and occurrence identifiers. Free-text task titles and descriptions can still
be sensitive; review any log or exported configuration before sharing it.

Download diagnostics from **Settings → Devices & services → Household Tasks →
three-dot menu → Download diagnostics**. Version, entry count, people count,
and task-definition count are also available under Home Assistant System
Health.

## Troubleshooting

- **Panel is missing:** restart Home Assistant and hard-refresh the browser.
- **No push notification:** verify the person's `notify.mobile_app_*` service
  and mobile-app permissions.
- **NFC does nothing:** scan once under **Settings → Tags**, copy the exact ID,
  ensure the definition is active, and confirm that the ID is not reused.
- **State task does not run:** check the exact entity state in Developer Tools;
  display labels often differ from raw state values.
- **Duplicate task:** enable duplicate suppression for state schedules and
  inspect the configured cooldown.

Enable debug logging temporarily:

```yaml
logger:
  logs:
    custom_components.household_tasks: debug
```

Never post unredacted storage files or configuration exports.

## Known limitations

- One Household Tasks config entry is supported per Home Assistant instance.
- The management panel currently ships in German and English.
- NFC identifies a tag and sometimes the scanner; it is not an authentication
  mechanism.
- The integration provides actions and an NFC event, but no custom Home
  Assistant trigger, condition, or blueprint platform.
- Browser-level automated tests for the custom panel are not yet included.

## Updating and removal

Export configuration and create a Home Assistant backup before a major-version
upgrade. Update through HACS and restart Home Assistant.

To remove the integration, export configuration if needed, delete its config
entry under **Settings → Devices & services**, restart Home Assistant, and
uninstall it in HACS. Removing the config entry removes the integration-owned
task store; restore it from a Home Assistant backup if required.

## Development

```bash
python -m pip install -e ".[dev]"
pre-commit install
make check
```

On Windows, or when local Python dependencies should stay isolated, run the
same checks in Docker:

```powershell
.\scripts\docker-check.ps1
```

The Docker development image caches the Home Assistant dependency stack.
Subsequent runs only mount the current source tree, then run pytest on four
workers while both Ruff checks run in parallel. Use `-Workers auto` to let
pytest use every CPU available to Docker, or `-NoBuild` to skip even the
cached image check when dependencies have not changed.

For an end-to-end test, copy or symlink
`custom_components/household_tasks` into a dedicated Home Assistant development
instance. CI runs unit tests, coverage reporting, Ruff, Home Assistant hassfest,
and HACS repository validation. The deterministic domain layer has an enforced
80% branch-aware coverage floor; the current local run is at 92%. SonarQube
receives a separate full-runtime report that includes lifecycle, engine,
configuration, diagnostics, and WebSocket code so untested orchestration paths
remain visible instead of being excluded from the quality gate. The current
full-runtime baseline is 39% and is enforced as a non-regression floor; it
should be raised as engine scenarios are moved into focused modules and tests.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow and
[RELEASING.md](RELEASING.md) for release procedure. Repository owners should
also complete the one-time [maintainer setup](docs/maintainer-setup.md) for
branch protection, SonarQube, security scanning, and release provenance.

### iPhone and iPad client

The recommended zero-cost iOS integration uses the
[official Home Assistant Custom Widget](docs/ios-widget.md). Household Tasks
creates a person-scoped inbox sensor and a safe action button for each person.
It also exposes an explicit next-task sensor, five stable read-only task slots,
and personal open, due-today, overdue, and blocked counters for native widgets.
The button sends task-specific actionable notifications whose completion,
claim, snooze, and help actions execute in the background without opening
another app. It needs neither an Apple Developer membership nor a separate
access token.

For richer standalone previews, the repository also includes an optional
Scriptable client. Scriptable always opens its host app for interactive taps,
so it is best treated as a display-oriented fallback rather than the primary
iOS interaction model.

The repository includes a native-feeling
[Scriptable widget and action client](clients/scriptable/README.md). It shows
the authenticated user's open tasks and supports completion, claiming,
checklists, snoozing, status changes, and help requests. The companion REST API
uses standard Home Assistant bearer authentication and never exposes notify
services, presence entities, access tokens, or the complete household
configuration.

For least privilege, create a dedicated non-administrator Home Assistant user
and link it to exactly one Household Tasks person. Scriptable stores the token
only in the iOS Keychain and keeps merely the secret-free task response as its
offline widget cache.

### Quality and security gates

- Unit and Home Assistant config-flow tests on supported Python versions
- Ruff linting and deterministic-domain coverage threshold
- hassfest and HACS repository validation
- SonarQube Server or SonarQube Cloud quality-gate analysis
- CodeQL for Python and JavaScript
- Gitleaks secret scanning and pull-request dependency review
- OpenSSF Scorecard supply-chain analysis
- SHA-pinned GitHub Actions and Dependabot updates
- Reproducible release archive with SHA-256 checksum and build provenance

## Project status

Version 3.0.0 is a community beta. The public configuration schema is versioned
and imports are validated, but production households should still keep regular
Home Assistant backups. See the
[immutable GitHub releases](https://github.com/domschmidt/ha-household-tasks/releases)
and the open issues for known work. `CHANGELOG.md` on the development branch is
the baseline; every release tag contains its generated, cumulative changelog.

## License

[MIT](LICENSE)

## Trademark notice

Home Assistant is a trademark of its respective owner. This project is an
independent community integration and is not affiliated with or endorsed by
the Home Assistant project.
