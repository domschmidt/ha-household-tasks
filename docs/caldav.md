# CalDAV server and Apple Reminders

Household Tasks includes a bidirectional, person-scoped CalDAV server for
VTODO clients such as Apple Reminders. Household Tasks remains the source of
truth. CalDAV is a synchronized view of native task occurrences, not a second
task database.

The implementation follows the task-relevant portions of
[CalDAV (RFC 4791)](https://www.rfc-editor.org/rfc/rfc4791),
[iCalendar (RFC 5545)](https://www.rfc-editor.org/rfc/rfc5545),
[WebDAV collection synchronization (RFC 6578)](https://www.rfc-editor.org/rfc/rfc6578),
and [CalDAV discovery (RFC 6764)](https://www.rfc-editor.org/rfc/rfc6764).

## What is supported

- CalDAV service, principal, calendar-home and calendar discovery;
- provisioned personal or household task calendars;
- `VTODO` read, create, update, complete, reopen, cancel and delete;
- structured checklist items as `RELATED-TO;RELTYPE=PARENT` VTODO subtasks;
- `VALARM`, due dates, status, percentage, priority, categories, URL, notes,
  created/updated/completed timestamps and stable UIDs;
- strong ETags and `If-Match`/`If-None-Match` preconditions;
- `calendar-query`, `calendar-multiget` and `sync-collection` REPORTs;
- persistent sync tokens, bounded tombstones and deletion propagation;
- client-side list name and color through `PROPPATCH`;
- read-only or read/write credentials, personal or household scope, optional
  expiry, claimable-task visibility, and checklist completion policy;
- app-password revocation, authentication throttling and security audit events.

CalDAV scheduling, attendees, inbox/outbox, free/busy and `VEVENT` are not
advertised. They solve meeting scheduling, not household task synchronization.
Extra calendars cannot be created with `MKCALENDAR`; every credential receives
one provisioned `tasks` calendar with a stable purpose and permission boundary.

## Prerequisites

1. Home Assistant must have a stable HTTPS URL that the client can reach.
2. The TLS certificate must be trusted by the iPhone or iPad. A private CA can
   be used if its root certificate is installed and trusted on every device.
3. Reverse proxies must pass the original HTTPS scheme correctly. Keep
   **Require HTTPS** enabled in production.
4. The Household Tasks person must exist before a personal credential is
   created.

The CalDAV server is disabled by default and has no default password.

## Enable the server

Open **Household Tasks → Settings → CalDAV for Apple Reminders**.

| Option | Meaning | Recommended |
| --- | --- | --- |
| Enable CalDAV server | Enables protocol requests. | On after HTTPS works |
| Require HTTPS | Rejects clear-text HTTP before credentials are processed. | On |
| List name | Name shown by the CalDAV client. | Household Tasks |
| Color | `#RRGGBB` or `#RRGGBBAA`. | Any |
| Description | Human-readable list description. | Optional |
| Synchronize completed tasks | Keeps completed/cancelled items visible in clients. Off prevents old tasks from reappearing. | Off |
| Completed task retention | Retention window when completed synchronization is enabled. | 90 days |
| Default reminder | Adds a display alarm before due time if none exists. | 0 |
| Create in client | Allows new reminders to become native ad-hoc tasks. | On |
| Edit/complete in client | Allows title, notes, due date, priority and state writes. | On |
| Delete in client | Converts DELETE into an audited cancellation and sync tombstone. | On |

Turning off one write operation applies immediately to every credential. A
read-only credential can never write even when the global switches allow it.

## Create an app password

Create a separate credential for every device. Do not reuse a Home Assistant
password or long-lived access token.

| Credential option | Meaning |
| --- | --- |
| Label | Identifies the device during later revocation. |
| Person | Native person credited for completions and owner of new tasks. |
| Personal scope | Shows assigned, helped and optionally claimable tasks. |
| Household scope | Shows every occurrence; useful for a shared admin device. |
| Read and write | Allows operations enabled by the global switches. |
| Read only | Projection and synchronization only. |
| Expiry | Optional automatic credential expiry. |
| Claimable tasks | Adds unassigned tasks the person is allowed to claim. |
| Complete checklist | Lets a parent completion also finish remaining checklist items. |

The generated password is displayed once. Only a PBKDF2-SHA256 verifier with a
random salt is persisted. Losing the password requires creating a replacement;
it cannot be recovered.

The copy buttons copy the exact server URL, username, or one-time password. If
the browser blocks its Clipboard API, the panel uses a local compatibility
fallback and reports a visible error if neither mechanism is available.

## Configure Apple Reminders

The exact Settings navigation varies by iOS version. Add a CalDAV account under
the Reminders account settings and enter:

- **Server:** the complete server URL shown in Household Tasks;
- **Username:** the generated CalDAV username;
- **Password:** the one-time app password;
- **Use SSL:** enabled.

Each projected reminder includes the responsible person's name in its title.
Its notes show the native description plus read-only status, priority, points,
and checklist progress. Household Tasks removes that generated detail block
before applying a client edit, so repeated offline round trips never duplicate
the metadata.

If iOS exposes an advanced path field, use
`/api/household_tasks/caldav/`. Automatic discovery is also available at
`/.well-known/caldav`.

After the first synchronization, the provisioned task list appears in
Reminders. Configure one personal account per household member if every person
should receive only their own list.

## Mapping

| Household Tasks | iCalendar VTODO |
| --- | --- |
| Occurrence ID | Stable UID and `X-HOUSEHOLD-OCCURRENCE-ID` |
| Name | SUMMARY |
| Description | DESCRIPTION |
| Due time | DUE in UTC |
| Open | NEEDS-ACTION |
| In progress | IN-PROCESS |
| Completed | COMPLETED, COMPLETED timestamp, 100% |
| Cancelled | CANCELLED |
| Priority | PRIORITY 1–9 |
| Checklist item | Child VTODO with parent relation |
| Dependencies | `RELATED-TO;RELTYPE=DEPENDS-ON` |
| Revision | Strong ETag and `X-HOUSEHOLD-REVISION` |
| Person | `X-HOUSEHOLD-ASSIGNEE` |
| Points | `X-HOUSEHOLD-POINTS` |
| Reminder | VALARM |

Unknown client `X-` properties are retained in bounded metadata so a round trip
does not silently discard client extensions.

## Offline synchronization and conflicts

Apple Reminders can mark a task completed while Home Assistant is unreachable.
The local change remains queued on the device. Nothing is changed or falsely
completed on the Home Assistant server while it is offline.

After connectivity returns, the client submits the VTODO:

1. If the ETag still matches, the completion is applied atomically and credited
   to the credential's person.
2. If Home Assistant changed the occurrence in the meantime, the stale
   `If-Match` receives HTTP 412. The client must fetch the latest representation
   instead of silently overwriting it.
3. Collection sync tokens return only changed or deleted resources. Expired
   tokens receive a standards-based `valid-sync-token` error and require a full
   refresh.

Completing a blocked task is rejected. Reopening a completed task removes its
awarded points from the previous actor. Already generated follow-up occurrences
remain in the audit history because silently deleting downstream work would be
unsafe.

## Security operations

- Use one credential per device and revoke it immediately when a device is
  lost, sold or reset.
- Prefer an expiry for temporary or guest devices.
- Keep household-wide credentials limited to trusted shared devices.
- Monitor the `household_tasks_caldav_security_event` event for credential
  creation and revocation.
- Authentication failures are rate-limited per remote address.
- Requests, credentials and responses are marked `no-store`; plaintext
  passwords are never persisted or included in diagnostics.
- Take a Home Assistant backup before restoring or moving the installation.
  The native task store and CalDAV credential/sync store must be restored from
  the same backup generation.

## Troubleshooting

### HTTP 401

The username/password is wrong, expired or revoked. Create a new device-specific
credential rather than changing the Home Assistant account password.

### HTTP 403 with “requires HTTPS”

Configure a valid Home Assistant external HTTPS URL and ensure the reverse proxy
passes the original scheme. Disabling the requirement is intended only for an
isolated local test network.

### Account verifies but no tasks appear

Check the credential's person and scope. A personal list includes tasks assigned
to that person, helper assignments, and optionally claimable work. Completed
tasks outside the configured retention window are intentionally omitted.

### A completion is rejected

The task may have an unresolved dependency, an incomplete checklist when parent
completion is disabled, or an ETag conflict from a concurrent edit. Refresh the
list and inspect the task history in Household Tasks.

### Reset a client safely

Revoke the old credential, remove the account from the device, create a new app
password and add the account again. Do not delete the native Household Tasks
store merely to rebuild a client cache.
