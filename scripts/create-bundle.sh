#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Write the checksum sidecars the Central Portal requires, then package
# the Maven repository as a Central bundle ZIP.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

M2REPO="$INPUT_M2REPO"
BUNDLE_PATH="${GITHUB_WORKSPACE}/central-bundle/bundle.zip"
mkdir -p "$(dirname "$BUNDLE_PATH")"
rm -f "$BUNDLE_PATH"

echo "Generating checksums..."
write_sidecars "$M2REPO"

# The ZIP keeps the Maven directory structure, paths relative to the
# repository root. An explicit file list keeps the excluded names out at
# every depth, which zip's -x patterns did not.
list_bundle_files "$M2REPO" | (cd "$M2REPO" && zip "$BUNDLE_PATH" -@)

BUNDLE_SIZE=$(stat -c%s "$BUNDLE_PATH" 2>/dev/null || stat -f%z "$BUNDLE_PATH")
echo "Bundle created: $BUNDLE_PATH ($(numfmt --to=iec "$BUNDLE_SIZE"))"
printf 'bundle_path=%s\nbundle-size=%s\n' "$BUNDLE_PATH" "$BUNDLE_SIZE" >> "$GITHUB_OUTPUT"
