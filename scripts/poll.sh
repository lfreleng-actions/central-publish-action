#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Poll the Central Portal until the uploaded deployment settles.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

central_init
require_deployment_id "$DEPLOYMENT_ID"

# USER_MANAGED stops at VALIDATED and waits for a person (or publish
# mode) to release it; AUTOMATIC succeeds only once Central publishes.
if [ "$INPUT_PUBLISHING_TYPE" = "USER_MANAGED" ]; then
  TARGET="VALIDATED"
else
  TARGET="PUBLISHED"
fi

RESULT=0
poll_deployment "$DEPLOYMENT_ID" "$TARGET" || RESULT=1
echo "deployment_status=$DEPLOYMENT_STATUS" >> "$GITHUB_OUTPUT"

if [ "$RESULT" -ne 0 ]; then
  if [ "$TARGET" = "PUBLISHED" ] && [ "$DEPLOYMENT_STATUS" = "VALIDATED" ]; then
    echo "::error::AUTOMATIC deployment $DEPLOYMENT_ID passed validation but Central has not published it"
  fi
  exit 1
fi

case "$DEPLOYMENT_STATUS" in
  PUBLISHED) echo "✅ Deployment PUBLISHED successfully!" ;;
  VALIDATED) echo "✅ Deployment VALIDATED (USER_MANAGED — not auto-published)" ;;
esac
