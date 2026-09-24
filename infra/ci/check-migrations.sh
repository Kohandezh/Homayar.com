#!/usr/bin/env bash
set -Eeuo pipefail

backend_root="${1:-Homayar/backend}"
backend_root="$(cd -- "${backend_root}" && pwd -P)"

if [[ -f "${backend_root}/alembic.ini" ]]; then
  migration_root="${backend_root}"
  config_file="alembic.ini"
elif [[ -f "${backend_root}/../alembic.ini" ]]; then
  migration_root="$(cd -- "${backend_root}/.." && pwd -P)"
  config_file="alembic.ini"
else
  printf 'ERROR: Alembic configuration not found under %s or its parent.\n' "${backend_root}" >&2
  exit 2
fi

cd -- "${migration_root}"
heads_output="$(python -m alembic -c "${config_file}" heads)"
head_count="$(awk '/\(head\)/ { count += 1 } END { print count + 0 }' <<<"${heads_output}")"
if [[ "${head_count}" -ne 1 ]]; then
  printf 'ERROR: Expected exactly one Alembic head, found %s.\n%s\n' "${head_count}" "${heads_output}" >&2
  exit 1
fi

python -m alembic -c "${config_file}" upgrade head
python -m alembic -c "${config_file}" check
current_output="$(python -m alembic -c "${config_file}" current)"
if ! grep -q '(head)' <<<"${current_output}"; then
  printf 'ERROR: Database is not at the Alembic head after upgrade.\n%s\n' "${current_output}" >&2
  exit 1
fi
