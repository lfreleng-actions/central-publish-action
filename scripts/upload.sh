#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Upload the bundle to the Central Portal and record the deployment ID.
set -euo pipefail

PUBLISHING_TYPE="$INPUT_PUBLISHING_TYPE"

# Generate Bearer token: base64(username:password)
AUTH_TOKEN=$(echo -n "${CENTRAL_USERNAME}:${CENTRAL_TOKEN}" | base64 -w0)
echo "::add-mask::$AUTH_TOKEN"

echo "Uploading bundle to Central Portal (publishingType=$PUBLISHING_TYPE)..."

RESPONSE=$(curl -sf -X POST \
  "${CENTRAL_URL}/api/v1/publisher/upload?publishingType=${PUBLISHING_TYPE}" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -F "bundle=@${BUNDLE_PATH};type=application/octet-stream" \
  2>&1) || {
    echo "::error::Upload failed. Response: $RESPONSE"
    exit 1
  }

# Response is just the deployment ID (plain text)
DEPLOYMENT_ID="$RESPONSE"
echo "Deployment ID: $DEPLOYMENT_ID"
echo "deployment_id=$DEPLOYMENT_ID" >> "$GITHUB_OUTPUT"
