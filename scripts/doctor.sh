#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

status=0

check_command() {
  local command_name="$1"
  local friendly_name="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'ok      %-18s %s\n' "$friendly_name" "$("$command_name" --version 2>&1 | head -n 1)"
  else
    printf 'missing %-18s required\n' "$friendly_name"
    status=1
  fi
}

check_setting() {
  local setting_name="$1"
  local requirement="$2"
  if [[ -n "${!setting_name:-}" ]]; then
    printf 'set     %-30s configured\n' "$setting_name"
  else
    printf 'unset   %-30s %s\n' "$setting_name" "$requirement"
  fi
}

printf 'EDGEOS environment doctor\n\n'
check_command node Node.js
check_command npm npm
check_command python3 Python
check_command uv uv
if command -v docker >/dev/null 2>&1; then
  printf 'ok      %-18s %s\n' Docker "$(docker --version 2>&1 | head -n 1)"
else
  printf 'optional %-18s not installed; required only for the container workflow\n' Docker
fi

if [[ -f .env ]]; then
  printf '\nok      .env               present (values are never printed)\n'
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  printf '\ninfo    .env               absent; copy .env.example for local configuration\n'
fi

printf '\nConfiguration readiness\n'
check_setting NEXT_PUBLIC_SUPABASE_URL 'needed for real accounts'
check_setting NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY 'needed for real accounts'
check_setting SUPABASE_SECRET_KEY 'needed only for privileged server account operations'
check_setting THE_ODDS_API_KEY 'needed for The Odds API live polling'
check_setting THE_ODDS_API_SPORTS 'official comma-separated sport keys required for worker polling'

printf '\nDemo mode requires no provider or Supabase credentials.\n'
exit "$status"

