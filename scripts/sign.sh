#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Create a detached ASCII-armoured signature for each deployable artifact.
set -euo pipefail

M2REPO="$INPUT_M2REPO"
SIGN_COUNT=0

# Sign all .jar and .pom files
find "$M2REPO" -type f \( -name "*.jar" -o -name "*.pom" -o -name "*.module" \) | \
while read -r file; do
  if [ -f "${file}.asc" ]; then
    echo "  Skip (already signed): $(basename "$file")"
    continue
  fi

  if [ -n "$GPG_PASSPHRASE" ]; then
    gpg --batch --pinentry-mode loopback --passphrase "$GPG_PASSPHRASE" \
      --local-user "$GPG_KEY_ID" --armor --detach-sign "$file"
  else
    gpg --batch --pinentry-mode loopback \
      --local-user "$GPG_KEY_ID" --armor --detach-sign "$file"
  fi

  SIGN_COUNT=$((SIGN_COUNT + 1))
done

ASC_COUNT=$(find "$M2REPO" -name "*.asc" | wc -l)
echo "Signed artifacts: $ASC_COUNT .asc files created"
echo "sign-count=$ASC_COUNT" >> "$GITHUB_OUTPUT"
