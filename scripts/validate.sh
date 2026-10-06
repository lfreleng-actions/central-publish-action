#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Validate the action inputs before any signing or network access.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

MODE="${INPUT_MODE:-upload}"
M2REPO="$INPUT_M2REPO"
SIGNING_METHOD="$INPUT_SIGNING_METHOD"
ERRORS=0

fail() {
  echo "::error::$1"
  ERRORS=$((ERRORS + 1))
}

case "$MODE" in
  upload|publish) ;;
  *) fail "Invalid mode '$MODE' (expected: upload or publish)" ;;
esac

check_central_url "${INPUT_CENTRAL_URL%/}" || ERRORS=$((ERRORS + 1))

[[ "$INPUT_POLL_TIMEOUT" =~ ^[1-9][0-9]*$ ]] ||
  fail "poll-timeout '$INPUT_POLL_TIMEOUT' must be a whole number of seconds above 0"
[[ "$INPUT_POLL_INTERVAL" =~ ^[1-9][0-9]*$ ]] ||
  fail "poll-interval '$INPUT_POLL_INTERVAL' must be a whole number of seconds above 0"

case "$INPUT_SKIP_VERIFICATION" in
  true|false) ;;
  *) fail "skip-verification '$INPUT_SKIP_VERIFICATION' must be 'true' or 'false'" ;;
esac

if [ "$MODE" = "publish" ]; then
  # Publish mode signs, bundles and uploads nothing: signing-method,
  # gpg-private-key and publishing-type play no part.
  if [ -z "$INPUT_DEPLOYMENT_ID" ]; then
    fail "mode 'publish' requires deployment-id"
  else
    require_deployment_id "$INPUT_DEPLOYMENT_ID" || ERRORS=$((ERRORS + 1))
  fi
  CHECK_M2REPO="$([ "$INPUT_SKIP_VERIFICATION" = "true" ] && echo false || echo true)"
else
  case "$SIGNING_METHOD" in
    gpg|sigul|none) ;;
    *) fail "Invalid signing-method '$SIGNING_METHOD' (expected: gpg, sigul, or none)" ;;
  esac
  if [ "$SIGNING_METHOD" = "gpg" ] && [ "$INPUT_GPG_KEY_PROVIDED" != "true" ]; then
    fail "signing-method is 'gpg' but gpg-private-key is empty"
  fi
  case "$INPUT_PUBLISHING_TYPE" in
    AUTOMATIC|USER_MANAGED) ;;
    *) fail "Invalid publishing-type '$INPUT_PUBLISHING_TYPE' (expected: AUTOMATIC or USER_MANAGED)" ;;
  esac
  if [ -n "$INPUT_DEPLOYMENT_ID" ]; then
    fail "deployment-id applies to mode 'publish' alone; upload mode creates a new deployment"
  fi
  CHECK_M2REPO="true"
fi

ARTIFACT_COUNT=0
if [ "$CHECK_M2REPO" = "true" ]; then
  if [ ! -d "$M2REPO" ]; then
    fail "m2repo-path '$M2REPO' does not exist or is not a directory"
  else
    ARTIFACT_COUNT=$(find "$M2REPO" -name "*.pom" | wc -l)
    if [ "$ARTIFACT_COUNT" -eq 0 ]; then
      fail "No .pom files found in '$M2REPO' — is this a valid Maven repo?"
    fi
  fi
fi

if [ "$ERRORS" -gt 0 ]; then
  exit 1
fi

echo "Mode: $MODE"
if [ "$MODE" = "upload" ]; then
  echo "Signing method: $SIGNING_METHOD"
fi
if [ "$CHECK_M2REPO" = "true" ]; then
  echo "Found $ARTIFACT_COUNT POM(s) in $M2REPO"
fi
echo "artifact-count=$ARTIFACT_COUNT" >> "$GITHUB_OUTPUT"
