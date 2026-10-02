#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Generate checksums and package the Maven repository as a Central bundle.
set -euo pipefail

M2REPO="$INPUT_M2REPO"
BUNDLE_DIR="${GITHUB_WORKSPACE}/central-bundle"
BUNDLE_PATH="${BUNDLE_DIR}/bundle.zip"
mkdir -p "$BUNDLE_DIR"

# Bundle must contain artifacts in Maven directory structure
# (relative paths from the repo root)
cd "$M2REPO"

# Central Portal REQUIRES .md5 and .sha1 checksums for each artifact
echo "Generating checksums..."
find . -type f \
  ! -name "*.md5" ! -name "*.sha1" ! -name "*.sha256" ! -name "*.sha512" \
  ! -name "maven-metadata.xml*" ! -name "_remote.repositories" \
  | while read -r file; do
    md5sum "$file" | awk '{print $1}' > "${file}.md5"
    sha1sum "$file" | awk '{print $1}' > "${file}.sha1"
  done

zip -r "$BUNDLE_PATH" . \
  -x "*.sha256" -x "*.sha512" \
  -x "maven-metadata.xml*" -x "_remote.repositories"

BUNDLE_SIZE=$(stat -c%s "$BUNDLE_PATH" 2>/dev/null || stat -f%z "$BUNDLE_PATH")
echo "Bundle created: $BUNDLE_PATH ($(numfmt --to=iec "$BUNDLE_SIZE"))"
echo "bundle_path=$BUNDLE_PATH" >> "$GITHUB_OUTPUT"
echo "bundle-size=$BUNDLE_SIZE" >> "$GITHUB_OUTPUT"
