#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Confirm the caller pre-signed every deployable artifact (signing-method=sigul).
set -euo pipefail

M2REPO="$INPUT_M2REPO"
MISSING=0
ASC_COUNT=0

# Every deployable artifact (same set GPG would sign) MUST have a
# detached signature produced by the caller (e.g. sigul-sign-action).
while read -r file; do
  if [ -f "${file}.asc" ]; then
    ASC_COUNT=$((ASC_COUNT + 1))
  else
    echo "::error::Missing signature for $(basename "$file") (.asc not found)"
    MISSING=$((MISSING + 1))
  fi
done < <(find "$M2REPO" -type f \
  \( -name "*.jar" -o -name "*.pom" -o -name "*.module" \))

if [ "$MISSING" -gt 0 ]; then
  echo "::error::signing-method 'sigul' requires pre-signed artifacts, but $MISSING signature(s) are missing"
  exit 1
fi

echo "Verified $ASC_COUNT pre-signed artifact(s)"
echo "sign-count=$ASC_COUNT" >> "$GITHUB_OUTPUT"
