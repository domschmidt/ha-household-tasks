# Scriptable client for iPhone and iPad

The Scriptable client provides a Home Screen widget and an interactive task
menu without duplicating Household Tasks configuration on the phone. It uses
the authenticated, versioned REST API shipped with Household Tasks 3.0.0.

## Requirements

- Household Tasks 3.0.0 or newer
- [Scriptable](https://scriptable.app/) on iOS or iPadOS
- a Home Assistant URL reachable from the device
- a Home Assistant long-lived access token

Use HTTPS, Home Assistant Cloud, or a trusted VPN whenever possible. A token is
equivalent to the permissions of its Home Assistant user. The recommended setup
is a dedicated, non-administrator Home Assistant user linked to exactly one
Household Tasks person.

## Installation

1. In Home Assistant, link the intended Household Tasks person to a Home
   Assistant user.
2. Sign in as that user, open the Home Assistant profile and create a long-lived
   access token.
3. Install Scriptable from the App Store.
4. Create a new Scriptable script named `Household Tasks` and copy the complete
   contents of `Household Tasks.js` into it.
5. Run the script once inside Scriptable.
6. Enter the externally reachable Home Assistant base URL and token. The person
   ID can remain empty when the HA user is linked to exactly one person. An
   administrator with multiple household people must enter a person ID.
7. Add a Scriptable widget to the Home Screen and select the `Household Tasks`
   script in the widget settings.

The URL and preferences are stored in the iOS Keychain. The access token is
stored separately in the Keychain and is never written into the script, widget
parameter, logs, or offline cache.

## Features

- small, medium, large, and extra-large widgets
- due-today, overdue, blocked, priority, points, and checklist indicators
- per-task menus for complete, claim, checklist, snooze, start, and help actions
- automatic refresh request every 15 minutes (the actual schedule is controlled
  by iOS)
- read-only offline fallback using the last successful, secret-free response
- German and English labels based on the device language
- secure first-run configuration and explicit warning for unencrypted HTTP

## API

The client calls:

```text
GET  /api/household_tasks/v1/tasks
POST /api/household_tasks/v1/tasks/{occurrence_id}/{action}
```

Both endpoints require the standard Home Assistant header:

```text
Authorization: Bearer <long-lived-access-token>
```

Supported actions are `complete`, `claim`, `snooze`, `help`, `decline`,
`status`, and `checklist`. The API derives or verifies `person_id` against the
authenticated Home Assistant user. Non-administrators cannot request another
person's task feed.

## Troubleshooting

- **401 Unauthorized:** create a new token and run the script's settings action.
- **Person required:** link the HA user to one Household Tasks person, or provide
  the person ID when using an administrator account.
- **Connection failed:** verify that the exact base URL is reachable in Safari
  from the iPhone. Do not append `/api` to the configured URL.
- **Stale widget:** iOS decides when widgets refresh. Open the widget or run the
  script to fetch immediately.
- **HTTP warning:** prefer HTTPS. Enable HTTP only for a trusted local network;
  the token otherwise travels without transport encryption.
