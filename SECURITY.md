# Security policy

## Supported versions

Security fixes are provided for the latest released major version.

| Version | Supported |
| --- | --- |
| 3.x | Yes |
| 2.x and older | No |

## Reporting a vulnerability

Do not open a public issue. Use GitHub's **Report a vulnerability** flow at
`https://github.com/domschmidt/ha-household-tasks/security/advisories/new`.
Include the affected version, impact, minimal reproduction, and suggested
mitigation if known.

You should receive acknowledgement within seven days. A target remediation
timeline will follow initial triage. Please allow a coordinated fix and release
before public disclosure.

## Sensitive data

Configuration exports and Home Assistant storage can contain names, entity
IDs, user mappings, device IDs, notification services, schedules, and NFC tag
IDs. Treat them as private. Built-in diagnostics redact known identifiers, but
contributors must still review attachments and logs before sharing them.

CalDAV credentials are device-specific app passwords. Their plaintext value is
shown once and never persisted. The integration stores a random salt and a
PBKDF2-SHA256 verifier in `household_tasks.caldav`, separate from the native
task store. Diagnostics expose only aggregate credential counts. Production
CalDAV access must use HTTPS; clear-text access is rejected by default.

The repository runs Gitleaks, CodeQL, dependency review, and OpenSSF Scorecard
in CI. GitHub secret scanning and push protection should additionally be
enabled in repository settings. SonarQube credentials belong only in GitHub
Actions secrets; never commit tokens or server certificates.

## Threat model

The integration trusts authenticated Home Assistant users according to their
permissions. Administrative WebSocket commands require an administrator. NFC
tags are identifiers, not authentication factors; do not use an NFC scan as
the sole authorization for safety-critical actions.
