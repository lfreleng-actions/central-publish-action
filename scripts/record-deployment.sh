#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Record the upload manifest in the m2repo: the deployment ID the upload
# returned and the SHA-256 of every file the uploaded bundle carried.
# Publish mode requires the deployment it releases to match this record
# exactly.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

M2REPO="$INPUT_M2REPO"
MANIFEST="$M2REPO/$MANIFEST_NAME"
require_deployment_id "$DEPLOYMENT_ID"
if [ ! -f "${BUNDLE_PATH-}" ]; then
  echo "::error::The uploaded bundle '${BUNDLE_PATH-}' is not a file"
  exit 1
fi

# Digest the ZIP that was uploaded, not the m2repo, which may have changed
# since. Extracting it sidesteps unzip's wildcard matching of entry names.
CONTENTS=$(mktemp -d)
trap 'rm -rf "$CONTENTS"' EXIT
unzip -q "$BUNDLE_PATH" -d "$CONTENTS"

RECORD=$( (cd "$CONTENTS" && find . -type f -print) | sed 's|^\./||' |
  LC_ALL=C sort | while IFS= read -r file; do
    printf '%s\t%s\n' "$file" "$(sha256_of < "$CONTENTS/$file")"
  done |
  jq -Rn --arg id "$DEPLOYMENT_ID" '{
    deploymentId: $id,
    files: (reduce (inputs | split("\t")) as [$path, $digest] ({}; .[$path] = $digest))
  }')
printf '%s\n' "$RECORD" > "$MANIFEST"

echo "Recorded $(jq '.files | length' "$MANIFEST") file(s) of deployment $DEPLOYMENT_ID from $BUNDLE_PATH in $MANIFEST"
echo "manifest_path=$MANIFEST" >> "$GITHUB_OUTPUT"
