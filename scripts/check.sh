#!/bin/sh

# Run independent quality gates concurrently. Do not use `set -e`: every
# process must be reaped so a fast failure cannot leave background work behind.
set -u

available_workers="$(nproc 2>/dev/null || printf '2')"
if [ "$available_workers" -gt 4 ]; then
    default_workers=4
else
    default_workers="$available_workers"
fi
pytest_workers="${PYTEST_WORKERS:-$default_workers}"

printf 'Running pytest (%s workers), Ruff lint, and Ruff format in parallel...\n' \
    "$pytest_workers"

python -m pytest -q -n "$pytest_workers" --dist=worksteal &
pytest_pid=$!
python -m ruff check . &
ruff_check_pid=$!
python -m ruff format --check . &
ruff_format_pid=$!

status=0

if ! wait "$pytest_pid"; then
    printf '\npytest failed\n' >&2
    status=1
fi
if ! wait "$ruff_check_pid"; then
    printf '\nRuff lint failed\n' >&2
    status=1
fi
if ! wait "$ruff_format_pid"; then
    printf '\nRuff format check failed\n' >&2
    status=1
fi

if [ "$status" -eq 0 ]; then
    printf '\nAll checks passed.\n'
fi

exit "$status"
