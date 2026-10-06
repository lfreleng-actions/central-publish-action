#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Upload the bundle to the Central Portal and record the deployment ID.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

PUBLISHING_TYPE="$INPUT_PUBLISHING_TYPE"
central_init

echo "Uploading bundle to Central Portal (publishingType=$PUBLISHING_TYPE)..."

REPLY_FILE=$(mktemp)
trap 'rm -f "$REPLY_FILE"' EXIT

# curl's own diagnostics go to the log on stderr; the reply body goes to
# a file, so neither can end up in the deployment ID.
HTTP_CODE=$(curl -sS -X POST -o "$REPLY_FILE" -w '%{http_code}' \
  --connect-timeout 30 \
  "${CENTRAL_URL}/api/v1/publisher/upload?publishingType=${PUBLISHING_TYPE}" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -F "bundle=@${BUNDLE_PATH};type=application/octet-stream") || HTTP_CODE="000"

if [[ ! "$HTTP_CODE" =~ ^2[0-9][0-9]$ ]]; then
  echo "::error::Upload failed (HTTP $HTTP_CODE). Response:"
  head -c 2000 "$REPLY_FILE" || true
  echo
  exit 1
fi

# The reply is the deployment ID as plain text; anything else in it
# breaks the UUID match.
DEPLOYMENT_ID=$(tr -d '[:space:]' < "$REPLY_FILE")
if ! require_deployment_id "$DEPLOYMENT_ID"; then
  echo "::error::Upload returned HTTP $HTTP_CODE without a deployment ID. Response:"
  head -c 2000 "$REPLY_FILE" || true
  echo
  exit 1
fi

echo "Deployment ID: $DEPLOYMENT_ID"
echo "deployment_id=$DEPLOYMENT_ID" >> "$GITHUB_OUTPUT"
