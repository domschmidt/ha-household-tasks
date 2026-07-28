# Release process

Every merged pull request creates exactly one release through GitHub Actions:

1. The pull request must pass tests, Ruff, hassfest, HACS validation, security
   scans, the Conventional Commit title check, and the SonarQube quality gate.
2. A maintainer verifies the title's release impact and squash-merges the pull
   request so the reviewed title becomes the commit message.
3. The protected `main` push resolves its associated merged pull request through
   GitHub's API. The release planner allocates the next Semantic Version from
   all immutable release tags and the reviewed pull request title.
4. It creates a detached release commit as a child of the exact merge commit.
   That commit contains the generated `CHANGELOG.md` entry and synchronized
   versions in `manifest.json`, `const.py`, and `pyproject.toml`.
5. The workflow pushes only the annotated `vMAJOR.MINOR.PATCH` tag, never a
   branch, then builds, attests, and publishes the release assets.

The workflow authenticates with GitHub's built-in, short-lived `GITHUB_TOKEN`;
it requires `contents: write` but no repository secret or personal token.
Parallel runs allocate tags optimistically: an atomic tag push wins, while a
colliding run fetches the winning tag and recalculates. The annotated tag stores
the pull request number, making retries idempotent and ensuring one release per
merged pull request without weakening `main` branch protection.

Use these pull request title conventions:

- `feat!:` or a `BREAKING CHANGE:` body footer creates a major release.
- `feat:` creates a minor release.
- Every other merged pull request creates a patch release, including `fix:`,
  `docs:`, `test:`, `ci:`, `chore:`, `refactor:`, and dependency updates.

The development branch intentionally retains its baseline version and changelog;
the authoritative generated metadata is stored in each immutable release tag.
Do not edit release versions, generated changelog sections, or tags manually.
Never rewrite or replace a published tag.

## Compatibility contract

The public compatibility contract includes Home Assistant actions and their
fields, emitted event payloads, the configuration export schema, persisted
configuration semantics, and documented automation behavior:

- **PATCH** fixes defects or contains other changes that do not require
  configuration or automation changes.
- **MINOR** adds backward-compatible behavior and may deprecate existing
  behavior.
- **MAJOR** may require users to migrate configuration, exports, dashboards, or
  automations.

Release tags always use the exact manifest version with a `v` prefix, for
example `v3.1.0`.
