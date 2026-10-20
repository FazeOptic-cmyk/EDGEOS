#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

printf 'Running backend checks...\n'
(
  cd apps/api
  uv lock --check
  uv run ruff check .
  uv run mypy edgeos
  uv run pytest
)

printf '\nRunning web checks...\n'
npm run lint --workspace=@edgeos/web
npm run typecheck --workspace=@edgeos/web
npm run test --workspace=@edgeos/web -- --run
npm run build --workspace=@edgeos/web

printf '\nRunning desktop host checks...\n'
npm run typecheck --workspace=@edgeos/desktop
npm run test --workspace=@edgeos/desktop

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  printf '\nValidating Compose configuration...\n'
  docker compose config --quiet
else
  printf '\nDocker is unavailable or its daemon is inaccessible; Compose validation skipped.\n'
fi

printf '\nEDGEOS verification passed.\n'

