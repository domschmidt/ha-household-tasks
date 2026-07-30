# Architecture

## Design goals

Household Tasks is local-first, UI-configured, restart-safe, and additive to
Home Assistant's native to-do model. It does not replace the to-do integration:
it orchestrates task definitions and records occurrence metadata around native
items.

## Components

```text
Config flow
    │
    ▼
Runtime engine ──────── Home Assistant to-do entity
    │   │   │
    │   │   ├────────── Mobile notifications and action events
    │   ├────────────── State, calendar, time, and NFC events
    └────────────────── Versioned Home Assistant storage
            ▲
            │ WebSocket API
            ▼
Packaged sidebar panel
```

- `engine.py` owns lifecycle, orchestration, Home Assistant listeners, native
  to-do synchronization, escalation, and persistence.
- `assignment.py`, `scheduling.py`, `workflows.py`, `analytics.py`, `resources.py`,
  `weather_rules.py`, `forecast_rules.py`, `nfc.py`, and `config_io.py` contain
  deterministic domain logic with focused unit tests.
- `ui.py` is the authenticated WebSocket boundary and enforces admin-only
  mutations.
- `frontend/` contains the packaged panel and its German/English translations.
- `diagnostics.py` and `system_health.py` expose support data without revealing
  household identifiers.

The engine intentionally coordinates side effects while decision logic is kept
in small, importable modules. New business rules should be implemented in a
pure module first and called by the engine.

## Data ownership

The selected Home Assistant `todo` entity owns the visible to-do items.
Household Tasks stores definitions, people mappings, counters, assignment
reasons, schedules, occurrence linkage, handovers, resource incidents,
seasonal execution keys, forecast decision traces, and history in Home
Assistant's storage API. Configuration exports are schema-versioned JSON
documents; operational seasonal locks deliberately remain outside imports.

Presence-aware assignment and handover resolution happen before an occurrence
is written to the native to-do list. Operational handover and incident history
is deliberately separate from editable configuration, so importing definitions
cannot silently rewrite the audit trail.

## Authorization

The Home Assistant config flow creates one config entry. WebSocket requests are
authenticated by Home Assistant. Mutating configuration, import, and settings
commands require an administrator. Operational commands also validate access
to the selected to-do entity.

## Failure model

- Storage is loaded before listeners and scheduled scans start.
- Listener unsubscribe callbacks are retained and called on unload.
- Imports are parsed and validated before replacing live configuration.
- Native to-do items are not deleted during integration removal.
- Forecast rules depend on the selected Home Assistant weather integration.
  Service failures and missing values suppress creation and retain an
  actionable trace; Household Tasks never contacts the provider directly.

## Compatibility and migrations

The integration version, storage version, and export schema are separate
concepts. A change to persisted data requires a storage migration. A breaking
user-facing change requires a major integration release and migration notes.
The minimum supported Home Assistant version is declared in `hacs.json`.

## Testing strategy

Pure domain modules are covered by deterministic unit tests. Home Assistant
lifecycle, config flow, service registration, authorization, and unload
behavior belong in integration tests using
`pytest-homeassistant-custom-component`. The branch-aware CI floor is 80% for
the deterministic domain layer and should only move upward. A separate,
unfiltered Python coverage report feeds SonarQube and deliberately includes the
engine and Home Assistant boundaries. Its 39% baseline is enforced as a
ratchet, not presented as sufficient end-state coverage. Frontend changes
require manual light and dark theme checks until browser automation is added.
