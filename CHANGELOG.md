# Changelog

All notable changes are documented here. The project follows Semantic
Versioning.

## 3.0.0 - 2026-07-27

### Added

- UI-managed people, task definitions, schedules, and global settings.
- Fixed, rotating, fair, and open task assignment with assignment explanations.
- Dependent tasks and completion-relative recurrence.
- State-triggered task creation with delay, cooldown, and duplicate protection.
- Presence-aware assignment with automatic pickup when an eligible person
  returns home.
- Time-limited household handovers for open and future responsibilities,
  including assignment audit history.
- Generic resource and consumption monitors with threshold conditions,
  cooldowns, and automatic recovery.
- Multi-action escalation chains that can notify, delegate, or open work for
  household claim.
- Actionable household retrospective insights for recurring delays, backlog,
  workload imbalance, and frequent reassignment.
- NFC tag create/complete actions with configurable visible feedback.
- Weekly review, 90-day analytics, and completion history.
- Versioned configuration export and validated import.
- German and English panel localization.
- Actionable push notifications, escalation, help, claim, and snooze flows.
- Focused unit tests for analytics, assignment, configuration import/export,
  NFC, scheduling, and workflows.
- HACS-ready repository layout and validation workflow.
- Privacy-aware diagnostics and Home Assistant System Health data.
- Community governance, security, support, and contribution documentation.
- SonarQube, CodeQL, Gitleaks, dependency review, and OpenSSF Scorecard CI.
- Reproducible release archives with checksums and provenance attestations.
- HACS brand assets and repository privacy contract tests.

### Changed

- GitHub Actions are pinned to reviewed commit SHAs.
- Personal author names and household-style test fixtures were replaced with
  contributor-owned and neutral metadata.
