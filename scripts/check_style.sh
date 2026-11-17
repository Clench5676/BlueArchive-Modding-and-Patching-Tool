#!/bin/sh
# Checks formatting and lint of the App code: ruff for Python, Biome for JavaScript and CSS.
#
#   scripts/check_style.sh           check every file the configs cover (as CI does)
#   scripts/check_style.sh --staged  check only the files staged for commit (as the commit hook does)
#   scripts/check_style.sh --fix     format and apply safe lint fixes in place
#
# Ruff runs from the project's dev dependencies (uv); Biome runs through npx at the version pinned in biome.jsonc.
# Exit 0: clean. Exit 1: a check failed. Exit 2: a tool could not run.

set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

biome_version=$(sed -n 's|.*biomejs.dev/schemas/\([0-9.]*\)/schema.json.*|\1|p' biome.jsonc)
[ -n "$biome_version" ] || { echo "check_style: no Biome version in biome.jsonc" >&2; exit 2; }
biome() { npx --yes "@biomejs/biome@$biome_version" "$@"; }
ruff() { uv run --quiet --frozen --only-dev ruff "$@"; }

status=0
case "${1:-}" in
    --fix)
        ruff format || status=1
        ruff check --fix || status=1
        biome check --write || status=1
        ;;
    --staged)
        py=$(git diff --cached --name-only --diff-filter=ACMR -- '*.py')
        if [ -n "$py" ]; then
            # --force-exclude keeps the configured exclusions for files named on the command line.
            # shellcheck disable=SC2086
            ruff format --check --force-exclude $py || status=1
            # shellcheck disable=SC2086
            ruff check --force-exclude $py || status=1
        fi
        biome ci --staged --no-errors-on-unmatched || status=1
        ;;
    '')
        ruff format --check || status=1
        ruff check || status=1
        biome ci || status=1
        ;;
    *)
        echo "usage: scripts/check_style.sh [--staged | --fix]" >&2
        exit 2
        ;;
esac
exit $status
