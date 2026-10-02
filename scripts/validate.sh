#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Validate the action inputs before any signing or network access.
set -euo pipefail

M2REPO="$INPUT_M2REPO"
SIGNING_METHOD="$INPUT_SIGNING_METHOD"

case "$SIGNING_METHOD" in
  gpg|sigul|none) ;;
  *)
    echo "::error::Invalid signing-method '$SIGNING_METHOD' (expected: gpg, sigul, or none)"
    exit 1
    ;;
esac

if [ "$SIGNING_METHOD" = "gpg" ] && [ "$INPUT_GPG_KEY_PROVIDED" != "true" ]; then
  echo "::error::signing-method is 'gpg' but gpg-private-key is empty"
  exit 1
fi

if [ ! -d "$M2REPO" ]; then
  echo "::error::m2repo-path '$M2REPO' does not exist or is not a directory"
  exit 1
fi

ARTIFACT_COUNT=$(find "$M2REPO" -name "*.pom" | wc -l)
if [ "$ARTIFACT_COUNT" -eq 0 ]; then
  echo "::error::No .pom files found in '$M2REPO' — is this a valid Maven repo?"
  exit 1
fi

echo "Signing method: $SIGNING_METHOD"
echo "Found $ARTIFACT_COUNT POM(s) in $M2REPO"
echo "artifact-count=$ARTIFACT_COUNT" >> "$GITHUB_OUTPUT"
