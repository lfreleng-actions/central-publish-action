#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Poll the Central Portal until the uploaded deployment settles.
set -euo pipefail

TIMEOUT="$INPUT_POLL_TIMEOUT"
INTERVAL="$INPUT_POLL_INTERVAL"

AUTH_TOKEN=$(echo -n "${CENTRAL_USERNAME}:${CENTRAL_TOKEN}" | base64 -w0)
echo "::add-mask::$AUTH_TOKEN"

echo "Polling deployment status (timeout: ${TIMEOUT}s, interval: ${INTERVAL}s)..."

ELAPSED=0
FINAL_STATUS="UNKNOWN"

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
  RESPONSE=$(curl -s -X POST \
    "${CENTRAL_URL}/api/v1/publisher/status?id=${DEPLOYMENT_ID}" \
    -H "Authorization: Bearer $AUTH_TOKEN" \
    -H "Content-Type: application/json" \
    -w "\n%{http_code}" \
    2>&1)

  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | sed '$d')

  if [ "$HTTP_CODE" != "200" ]; then
    echo "  Poll request failed (HTTP $HTTP_CODE), retrying..."
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
    continue
  fi

  STATUS=$(echo "$BODY" | jq -r '.deploymentState // .state // "UNKNOWN"')
  echo "  [$ELAPSED s] Status: $STATUS"

  case "$STATUS" in
    PUBLISHED)
      FINAL_STATUS="PUBLISHED"
      echo "✅ Deployment PUBLISHED successfully!"
      break
      ;;
    VALIDATED)
      FINAL_STATUS="VALIDATED"
      if [ "$INPUT_PUBLISHING_TYPE" = "USER_MANAGED" ]; then
        echo "✅ Deployment VALIDATED (USER_MANAGED — not auto-published)"
        break
      fi
      echo "  Waiting for publication..."
      ;;
    FAILED)
      FINAL_STATUS="FAILED"
      echo "::error::Deployment FAILED"
      echo "$BODY" | jq '.errors // .' 2>/dev/null || echo "$BODY"
      exit 1
      ;;
    PENDING|VALIDATING|PUBLISHING)
      # Still in progress
      ;;
    *)
      echo "  Unknown status: $STATUS"
      ;;
  esac

  sleep "$INTERVAL"
  ELAPSED=$((ELAPSED + INTERVAL))
done

if [ "$ELAPSED" -ge "$TIMEOUT" ] && [ "$FINAL_STATUS" != "PUBLISHED" ] && \
   [ "$FINAL_STATUS" != "VALIDATED" ]; then
  echo "::error::Timed out waiting for deployment (last status: $FINAL_STATUS)"
  exit 1
fi

echo "deployment_status=$FINAL_STATUS" >> "$GITHUB_OUTPUT"
