#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Create a detached ASCII-armoured signature for each deployable artifact.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

M2REPO="$INPUT_M2REPO"
SIGN_COUNT=0
SKIP_COUNT=0

PASSPHRASE_ARGS=()
if [ -n "$GPG_PASSPHRASE" ]; then
  PASSPHRASE_ARGS=(--passphrase "$GPG_PASSPHRASE")
fi

# Sign every file the bundle carries, other than checksums and signatures
while IFS= read -r file; do
  if [ -f "$M2REPO/${file}.asc" ]; then
    echo "  Skip (already signed): $file"
    SKIP_COUNT=$((SKIP_COUNT + 1))
    continue
  fi

  gpg --batch --pinentry-mode loopback "${PASSPHRASE_ARGS[@]}" \
    --local-user "$GPG_KEY_ID" --armor --detach-sign "$M2REPO/$file"

  SIGN_COUNT=$((SIGN_COUNT + 1))
done < <(list_signable_files "$M2REPO")

echo "Signed artifacts: $SIGN_COUNT new, $SKIP_COUNT already signed"
echo "sign-count=$((SIGN_COUNT + SKIP_COUNT))" >> "$GITHUB_OUTPUT"
