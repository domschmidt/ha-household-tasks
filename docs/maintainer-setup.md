# Maintainer setup

The repository contains the workflows, but several enterprise controls require
one-time configuration in the hosting organization.

## Repository identity

Before the first release, verify every occurrence of
`domschmidt/ha-household-tasks` and `@domschmidt`. These values are public
publisher metadata required by HACS, not household runtime data. Replace them
consistently if the project moves to an organization.

Enable GitHub Issues, Discussions, the dependency graph, Dependabot alerts,
private vulnerability reporting, secret scanning, and push protection. Add the
topics `home-assistant`, `hacs`, `household`, `tasks`, and
`custom-integration`.

Under **Settings → Actions → General → Workflow permissions**, select **Read and
write permissions**.

The release workflow uses GitHub's built-in, short-lived `GITHUB_TOKEN`. No
repository secret or personal access token is required. It never writes to
`main`: generated release metadata lives in a detached commit reachable through
the immutable release tag. Strict pull-request protection can therefore remain
enabled for every branch update. Releases run from the resulting protected
`main` push, so merged contributions from forks receive the same write-capable,
short-lived token without executing unreviewed fork code.

## Branch protection

Protect `main` as far as the selected release model permits. Recommended
settings for human contributions:

- allow squash merging, use the pull request title as the default squash commit
  title, and disable merge commits and rebase merging so the SemVer action sees
  the reviewed Conventional Commit title;
- require one approving review and dismiss stale approvals;
- require conversation resolution;
- block force pushes and branch deletion;
- require signed commits where the contributor workflow supports them;
- require linear history;
- require the Tests, Ruff, hassfest, HACS, CodeQL, Gitleaks, dependency-review,
  and SonarQube quality-gate checks;
- require branches to be current before merge;
- include administrators unless emergency policy says otherwise.

If repository rules also protect tags, allow the release workflow to create new
`v*` tags while continuing to block tag updates and deletions. Release tags are
immutable; the workflow never force-pushes or replaces them.

## SonarQube

The workflow supports both SonarQube Server and SonarQube Cloud.

Create these repository or organization settings:

| Name | Type | Required | Purpose |
| --- | --- | --- | --- |
| `SONAR_TOKEN` | Actions secret | Yes | Scoped project-analysis token |
| `SONAR_PROJECT_KEY` | Actions variable | Yes | Existing Sonar project key |
| `SONAR_HOST_URL` | Actions variable | Server only | HTTPS URL of SonarQube Server |
| `SONAR_ORGANIZATION` | Actions variable | Cloud only | SonarQube Cloud organization key |
| `SONAR_ROOT_CERT` | Actions secret | Optional | Additional trusted PEM certificate |

Use a project-scoped token with analysis permission only. Do not use a global
administrator token. Configure the Sonar quality gate as a required GitHub
check. The scanner receives `coverage.xml` from the same test run and waits for
the gate result. Pushes to `main` fail until SonarQube is configured; pull
requests from forks may skip the scan because GitHub correctly withholds
protected secrets.

## Releases

Do not create tags or edit version files manually. Every merged pull request
creates a release. `feat:` produces a minor release, a breaking-change marker
produces a major release, and every other title produces a patch release.

After a pull request is merged, the release planner generates the version and
`CHANGELOG.md` in a detached release commit based on the exact merge commit. It
synchronizes the remaining version sources, builds `household_tasks.zip` and
its SHA-256 checksum, pushes only the annotated `vMAJOR.MINOR.PATCH` tag, creates
the GitHub release, and attaches both files. Atomic tag allocation retries
collisions, so concurrent merges still receive distinct releases. The workflow
also publishes a GitHub artifact attestation.

Release signing and the GitHub Actions OIDC identity establish provenance.
Verify an archive with:

```bash
gh attestation verify household_tasks.zip --repo OWNER/ha-household-tasks
```

## HACS publication

The HACS and hassfest checks must pass before requesting inclusion in the
default HACS catalog. Keep `brand/icon.png` and `brand/icon@2x.png` in the
repository and create at least one GitHub release. HACS also expects a public
repository description, issues enabled, and appropriate topics.
