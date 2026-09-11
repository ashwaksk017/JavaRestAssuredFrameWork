#!/usr/bin/env bash
# POSIX helper for GitLab (maven image has no Python). Same contract as allure_history.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CMD="${1:-}"
HISTORY_DIR="${ALLURE_HISTORY_DIR:-allure-history}"
RESULTS_DIR="${ALLURE_RESULTS_DIR:-target/allure-results}"
REPORT_DIR="${ALLURE_REPORT_DIR:-target/allure-report}"

restore() {
  mkdir -p "${RESULTS_DIR}/history"
  if [ -d "${HISTORY_DIR}" ] && [ -n "$(ls -A "${HISTORY_DIR}" 2>/dev/null || true)" ]; then
    cp -a "${HISTORY_DIR}/." "${RESULTS_DIR}/history/"
    echo "Restored Allure history into ${RESULTS_DIR}/history"
  else
    echo "No Allure history cache; this report starts a new trend"
  fi
}

save() {
  if [ -d "${REPORT_DIR}/history" ] && [ -n "$(ls -A "${REPORT_DIR}/history" 2>/dev/null || true)" ]; then
    rm -rf "${HISTORY_DIR}"
    mkdir -p "${HISTORY_DIR}"
    cp -a "${REPORT_DIR}/history/." "${HISTORY_DIR}/"
    echo "Saved Allure history to ${HISTORY_DIR}"
  else
    echo "No report history to save (did allure:report run?)"
  fi
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

write_executor() {
  if [ "${GITLAB_CI:-}" != "true" ] && [ -z "${CI_PIPELINE_ID:-}" ]; then
    return 0
  fi
  mkdir -p "${RESULTS_DIR}"
  iid="${CI_PIPELINE_IID:-${CI_PIPELINE_ID:-0}}"
  case "${iid}" in
    ''|*[!0-9]*) iid=0 ;;
  esac
  pages="${CI_PAGES_URL:-}"
  extra=""
  if [ -n "${pages}" ]; then
    extra="$(printf ',\n  "reportUrl": "%s"' "$(json_escape "${pages}")")"
  fi
  cat > "${RESULTS_DIR}/executor.json" <<EOF
{
  "name": "GitLab CI",
  "type": "gitlab",
  "url": "$(json_escape "${CI_PROJECT_URL:-}")",
  "buildOrder": ${iid},
  "buildName": "$(json_escape "${CI_PIPELINE_ID:-}")",
  "buildUrl": "$(json_escape "${CI_PIPELINE_URL:-}")",
  "reportName": "Allure"${extra}
}
EOF
  echo "Wrote ${RESULTS_DIR}/executor.json"
}

case "${CMD}" in
  restore)
    restore
    write_executor
    ;;
  save)
    save
    ;;
  executor)
    write_executor
    ;;
  *)
    echo "Usage: $0 {restore|save|executor}" >&2
    exit 2
    ;;
esac
