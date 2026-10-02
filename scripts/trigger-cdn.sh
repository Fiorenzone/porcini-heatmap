#!/usr/bin/env bash
# Ping esterno → workflow_dispatch (GitHub schedule è inaffidabile).
# Uso locale: GITHUB_TOKEN=ghp_… ./scripts/trigger-cdn.sh
# Uso cron-job.org (ogni 3h):
#   URL:  https://api.github.com/repos/Fiorenzone/porcini-heatmap/actions/workflows/cdn.yml/dispatches
#   Method: POST
#   Headers:
#     Accept: application/vnd.github+json
#     Authorization: Bearer ghp_…   (fine-grained: Actions Read/Write su questo repo)
#     X-GitHub-Api-Version: 2022-11-28
#     Content-Type: application/json
#   Body: {"ref":"main"}
set -euo pipefail
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [[ -z "$TOKEN" ]]; then
  echo "manca GITHUB_TOKEN" >&2
  exit 1
fi
REPO="${GITHUB_REPOSITORY:-Fiorenzone/porcini-heatmap}"
WORKFLOW="${CDN_WORKFLOW:-cdn.yml}"
REF="${CDN_REF:-main}"
curl -fsS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches" \
  -d "{\"ref\":\"${REF}\"}"
echo "dispatch ok ${REPO} ${WORKFLOW} @${REF}"
