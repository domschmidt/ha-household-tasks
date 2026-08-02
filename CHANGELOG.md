# Changelog

All notable changes are documented here. The project follows Semantic
Versioning.

## 4.9.1 - 2026-08-02

### Fixed

- fix(calendar): keep simultaneous events distinct ([#25](https://github.com/domschmidt/ha-household-tasks/pull/25))

## 4.9.0 - 2026-08-01

### Added

- feat(ui): add guided task wizard ([#24](https://github.com/domschmidt/ha-household-tasks/pull/24))

## 4.8.1 - 2026-08-01

### Changed

- test: add Playwright UI regression suite ([#23](https://github.com/domschmidt/ha-household-tasks/pull/23))

## 4.8.0 - 2026-08-01

### Added

- feat: expose evidence in task history ([#22](https://github.com/domschmidt/ha-household-tasks/pull/22))

## 4.7.0 - 2026-08-01

### Added

- feat: add deep links for panel tabs ([#21](https://github.com/domschmidt/ha-household-tasks/pull/21))

## 4.6.0 - 2026-08-01

### Added

- feat: preview calendar tasks in weekly plan ([#20](https://github.com/domschmidt/ha-household-tasks/pull/20))

## 4.5.0 - 2026-08-01

### Added

- feat: map calendar events to task names ([#19](https://github.com/domschmidt/ha-household-tasks/pull/19))

## 4.4.0 - 2026-08-01

### Added

- feat: add task widgets and explicit absence policies ([#18](https://github.com/domschmidt/ha-household-tasks/pull/18))

## 4.3.0 - 2026-07-31

### Added

- feat(ui): add week projections and widget recipes ([#17](https://github.com/domschmidt/ha-household-tasks/pull/17))

## 4.2.0 - 2026-07-31

### Added

- feat(ios): add official HA widget integration ([#16](https://github.com/domschmidt/ha-household-tasks/pull/16))

## 4.1.0 - 2026-07-31

### Added

- feat(ios): add Scriptable task client ([#15](https://github.com/domschmidt/ha-household-tasks/pull/15))

## 4.0.0 - 2026-07-31

### Added

- feat!: replace HA todo with native task store ([#14](https://github.com/domschmidt/ha-household-tasks/pull/14))

## 3.2.1 - 2026-07-30

### Fixed

- fix(ui): ignore hidden inline form controls ([#13](https://github.com/domschmidt/ha-household-tasks/pull/13))

## 3.2.0 - 2026-07-30

### Added

- feat: add advanced household automation and forecast planning ([#12](https://github.com/domschmidt/ha-household-tasks/pull/12))

## 3.1.1 - 2026-07-30

### Changed

- perf(dev): parallelize local quality checks ([#11](https://github.com/domschmidt/ha-household-tasks/pull/11))

## 3.1.0 - 2026-07-30

### Added

- feat(panel): add guided resource setup ([#10](https://github.com/domschmidt/ha-household-tasks/pull/10))

## 3.0.3 - 2026-07-29

### Fixed

- fix(ci): use valid CodeQL action commit ([#9](https://github.com/domschmidt/ha-household-tasks/pull/9))

## 3.0.2 - 2026-07-28

### Fixed

- fix(ci): repair Scorecard and SonarCloud scans ([#8](https://github.com/domschmidt/ha-household-tasks/pull/8))

## 3.0.1 - 2026-07-28

### Fixed

- fix(ci): make pytest imports and HA lifecycle deterministic ([#6](https://github.com/domschmidt/ha-household-tasks/pull/6))

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
