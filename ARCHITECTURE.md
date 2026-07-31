# Architecture

Household Tasks is a local-first Home Assistant integration with an owned task
domain. Home Assistant supplies lifecycle, authentication, entities, events,
services, notifications, and atomic persistence. Household Tasks is the single
source of truth for definitions and occurrences; no external task-list entity
is required.

## Runtime topology

```text
Config entry
  └─ HouseholdTaskEngine
       ├─ definitions, people, rules and monitors
       ├─ native occurrences and bounded event journal
       ├─ scheduler, weather/state/calendar evaluation
       ├─ assignment, escalation, NFC and notification workflows
       ├─ Home Assistant Store (atomic local persistence)
       ├─ Home Assistant services, events and Assist intents
       └─ authenticated WebSocket API
            └─ sidebar panel
```

The sidebar panel uses the authenticated WebSocket API. Small external clients
use a separate, versioned REST API that returns only a person-scoped task
projection; the included Scriptable iOS/iPadOS widget is its first consumer.

Pure domain modules contain scheduling, assignment, weather, forecasting,
analytics, import/export, productivity, NFC, and integrity logic. `engine.py`
coordinates those modules at the Home Assistant boundary. `ui.py` is a thin
authenticated transport adapter; the browser never writes storage directly.

## Native task model

A definition describes how future work is generated. An occurrence is the
independent unit of work created from that definition. Each occurrence stores:

- stable occurrence ID and definition snapshot;
- `open`, `in_progress`, `waiting`, `blocked`, `completed`, or `cancelled`;
- created/updated/resolved timestamps and an incrementing revision;
- assignee, assignment explanation, due date, priority and optional due window;
- structured checklist items with completion actor and timestamp;
- explicit occurrence dependencies;
- notification, escalation, attachment and seasonal metadata.

Writes support an optional expected revision. A stale client receives a
validation error and must reload instead of overwriting a concurrent change.
Blocked work cannot complete until every prerequisite is terminal. Completion
may require all checklist items. Completing or cancelling a prerequisite
atomically releases eligible dependents.

## Persistence and migration

The integration uses Home Assistant's `Store` under `household_tasks.state`.
The container version remains compatible with existing installations; an
internal `task_schema_version` performs domain migrations after loading.

The version-2 migration converts legacy mirrored occurrences in place:

- external item identifiers are removed;
- status is derived from the prior resolved flag;
- revision and lifecycle timestamps are initialized;
- definition checklists become occurrence-local checklist state;
- the bounded journal receives a `store_migrated` event.

Migration operates on a copy and is idempotent. Home Assistant's atomic store
write remains the durability boundary. Operators should still take a Home
Assistant backup before major-version upgrades.

## Audit and observability

Every material task transition appends a structured journal event containing a
random event ID, event type, timestamp, optional actor, occurrence ID, and safe
details. The journal is capped at 2,000 entries. The panel exposes the history
for each occurrence and diagnostics report schema and aggregate counts.

Every successful persisted write emits `household_tasks_updated` with schema,
open count, and total count. NFC handling additionally emits
`household_tasks_nfc_action`. Configuration health validates entity and service
references, duplicate definitions, dependency cycles, occurrence references,
status values, and checklist IDs.

## Security and permissions

Configuration mutation, imports, monitor changes, and household administration
require a Home Assistant administrator. Operational task actions are available
to administrators and users explicitly linked to a configured household person.
The external-client API verifies an optional `person_id` against the bearer
token's Home Assistant user, so a non-administrator cannot select another
household identity. Responses use `Cache-Control: no-store`; Scriptable stores
credentials only in the iOS Keychain and caches only the secret-free task
projection.
Attachments are bounded by count, size, and MIME type. URLs require HTTPS.
Diagnostics redact household identifiers; no telemetry or external account is
used.

## Failure behavior

- Invalid configuration is rejected before replacing active configuration.
- Rule previews and projections are side-effect free.
- Notification failure is isolated and does not corrupt task state.
- Revision conflicts prevent lost updates.
- Unknown or cyclic dependencies surface in health checks and validation.
- Unloading removes listeners, timers, intents, and panel registration while
  persisted state remains available for the next setup.

## Quality boundaries

Deterministic modules are unit tested with branch coverage. Integration tests
exercise config-entry migration, persistence, WebSocket access, services,
weather chains, seasonal fan-out, dependencies, checklists, NFC, notifications,
handover, monitors, unload/reload, and failure paths. CI additionally runs Ruff,
hassfest, HACS validation, dependency review when supported, and SonarQube.
