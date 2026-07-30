# Contributing

## Pull request titles

Use Conventional Commit syntax for pull request titles because squash-merged
titles drive automated Semantic Versioning:

- `fix: handle an unavailable todo entity`
- `feat: add a household summary sensor`
- `feat!: change the configuration export schema`

`feat:` creates a minor release and `feat!:` creates a major release. Every
other merged pull request creates a patch release, including `docs:`, `test:`,
`ci:`, `chore:`, `refactor:`, and dependency updates. Maintainers must correct
a pull request title before merging if its release impact is inaccurate.

Thank you for improving Household Tasks. Contributions of code, translations,
documentation, reproducible bug reports, and household use cases are welcome.

## Before opening work

Use GitHub Discussions for broad ideas and an issue for a scoped defect or
feature. For substantial behavior or schema changes, agree on the design in an
issue before implementation. Security reports must follow
[SECURITY.md](SECURITY.md).

## Development setup

Requirements are Python 3.12+ and Git.

```bash
git clone https://github.com/domschmidt/ha-household-tasks.git
cd ha-household-tasks
python -m pip install -e ".[dev]"
pre-commit install
```

Run the quality checks before every pull request:

```bash
make check
```

For an isolated, cached Docker environment on Windows:

```powershell
.\scripts\docker-check.ps1
```

For an end-to-end check, copy or symlink the integration into a dedicated Home
Assistant development instance. Do not commit Home Assistant storage,
databases, logs, secrets, backups, NFC tag IDs, or household exports.

## Code expectations

- Keep orchestration in `engine.py` and put deterministic decisions in a
  focused domain module.
- Add tests for new behavior and regression tests for fixes.
- Use asynchronous Home Assistant APIs; never block the event loop.
- Register global actions in `async_setup` and clean up config-entry listeners
  on unload.
- Add English source strings and German translations for user-facing text.
- Preserve backward compatibility or provide an explicit migration.
- Update README and the user guide for user-visible changes. The release
  workflow generates the versioned changelog entry from the pull request.

## Pull requests

Keep a pull request focused and explain the user outcome. Complete the pull
request checklist, include screenshots for panel changes, and avoid unrelated
formatting. Maintainers may request changes to keep schemas and interaction
patterns consistent.

By contributing, you agree that your contribution is licensed under the
project's MIT License.
