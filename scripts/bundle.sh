#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Generate checksums and package the Maven repository as a Central bundle.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

M2REPO="$INPUT_M2REPO"
BUNDLE_DIR="${GITHUB_WORKSPACE}/central-bundle"
BUNDLE_PATH="${BUNDLE_DIR}/bundle.zip"
mkdir -p "$BUNDLE_DIR"
rm -f "$BUNDLE_PATH"

# Central Portal REQUIRES .md5 and .sha1 checksums for each artifact
echo "Generating checksums..."
while IFS= read -r file; do
  md5sum "$M2REPO/$file" | awk '{print $1}' > "$M2REPO/${file}.md5"
  sha1sum "$M2REPO/$file" | awk '{print $1}' > "$M2REPO/${file}.sha1"
done < <(list_payload_files "$M2REPO")

# Bundle must contain artifacts in Maven directory structure (relative
# paths from the repo root). An explicit file list keeps the excluded
# names out at every depth, which zip's -x patterns did not.
list_bundle_files "$M2REPO" | (cd "$M2REPO" && zip "$BUNDLE_PATH" -@)

BUNDLE_SIZE=$(stat -c%s "$BUNDLE_PATH" 2>/dev/null || stat -f%z "$BUNDLE_PATH")
echo "Bundle created: $BUNDLE_PATH ($(numfmt --to=iec "$BUNDLE_SIZE"))"
echo "bundle_path=$BUNDLE_PATH" >> "$GITHUB_OUTPUT"
echo "bundle-size=$BUNDLE_SIZE" >> "$GITHUB_OUTPUT"
