#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Drop the deployment a CI run of this repository staged, so test
# artefacts never pile up on the Central Portal account, where one click
# on "Publish" would release them for good. CI tooling, not a step of the
# action. Push, schedule and pull request runs drop a VALIDATED or FAILED
# deployment once validation settles; any other run, workflow_dispatch
# included, keeps its deployment for inspection. A PUBLISHING or
# PUBLISHED deployment is never touched. Any other outcome fails with an
# error naming the deployment, so it cannot be left behind unnoticed.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

require_deployment_id "$DEPLOYMENT_ID"
central_init
DEPLOYMENT_STATUS="UNKNOWN"

case "${EVENT_NAME-}" in
  push|schedule|pull_request) ;;
  *)
    central_status "$DEPLOYMENT_ID" || true
    case "$DEPLOYMENT_STATUS" in
      PUBLISHING|PUBLISHED)
        echo "::notice::Deployment $DEPLOYMENT_ID is $DEPLOYMENT_STATUS"
        ;;
      *)
        echo "::notice::Keeping deployment $DEPLOYMENT_ID ($DEPLOYMENT_STATUS) of this ${EVENT_NAME:-unknown} run; drop it in the Central Portal once inspected"
        ;;
    esac
    exit 0
    ;;
esac

# Wait out PENDING and VALIDATING: the Portal drops only settled
# deployments. A FAILED deployment makes this return 1, which is fine.
poll_deployment "$DEPLOYMENT_ID" SETTLED || true

case "$DEPLOYMENT_STATUS" in
  VALIDATED|FAILED) ;;
  PUBLISHING|PUBLISHED)
    echo "::notice::Deployment $DEPLOYMENT_ID is $DEPLOYMENT_STATUS; not dropping it"
    exit 0
    ;;
  *)
    echo "::error::Could not drop deployment $DEPLOYMENT_ID: it is $DEPLOYMENT_STATUS, not VALIDATED or FAILED; drop it in the Central Portal"
    exit 1
    ;;
esac

REPLY_FILE=$(mktemp)
trap 'rm -f "$REPLY_FILE"' EXIT
HTTP_CODE=$(curl -sS -X DELETE -o "$REPLY_FILE" -w '%{http_code}' \
  --connect-timeout 30 --max-time 120 \
  "${CENTRAL_URL}/api/v1/publisher/deployment/${DEPLOYMENT_ID}" \
  -H "Authorization: Bearer $AUTH_TOKEN") || HTTP_CODE="000"

if [[ ! "$HTTP_CODE" =~ ^2[0-9][0-9]$ ]]; then
  echo "::error::Could not drop deployment $DEPLOYMENT_ID ($DEPLOYMENT_STATUS): HTTP $HTTP_CODE; drop it in the Central Portal. Response:"
  head -c 2000 "$REPLY_FILE" || true
  echo
  exit 1
fi
echo "Dropped deployment $DEPLOYMENT_ID ($DEPLOYMENT_STATUS)"
