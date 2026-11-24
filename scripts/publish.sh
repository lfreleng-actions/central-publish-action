#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Publish an existing deployment by ID (mode=publish), after checking that
# it, its upload manifest and the signed m2repo staged earlier agree on
# every file and every byte.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

DEPLOYMENT_ID="$INPUT_DEPLOYMENT_ID"
M2REPO="$INPUT_M2REPO"
DEPLOYMENT_STATUS="UNKNOWN"
VERIFIED=0
EXPECTED=""

case "${INPUT_DRY_RUN-}" in
  true|false) ;;
  *)
    echo "::error::dry-run '${INPUT_DRY_RUN-}' must be 'true' or 'false'"
    exit 1
    ;;
esac
require_deployment_id "$DEPLOYMENT_ID"
central_init
echo "deployment_id=$DEPLOYMENT_ID" >> "$GITHUB_OUTPUT"
trap 'echo "deployment_status=$DEPLOYMENT_STATUS" >> "$GITHUB_OUTPUT"
      echo "verified_file_count=$VERIFIED" >> "$GITHUB_OUTPUT"
      [ -z "$EXPECTED" ] || rm -f "$EXPECTED"' EXIT

# verify_manifest: the upload manifest in the m2repo must name this
# deployment and list exactly the files, with the same SHA-256, that
# EXPECTED holds. This verifier takes the deployment's complete file
# inventory from that record of the upload, not from the Portal.
verify_manifest() {
  local manifest="$M2REPO/$MANIFEST_NAME" recorded recorded_id line problems=0
  if [ ! -f "$manifest" ]; then
    echo "::error::Publish mode needs the upload manifest $manifest, which upload mode writes into m2repo-path once the upload succeeds. Restore the m2repo saved after that upload, or set skip-verification: true to publish unchecked."
    return 1
  fi
  # Called under ||, where errexit is off, so failures return explicitly
  if ! recorded=$(jq -r '
      if (.deploymentId | type) == "string" and (.files | type) == "object"
        and all(.files[]; type == "string")
      then .deploymentId, (.files | to_entries[] | "\(.key)\t\(.value)")
      else error("not an upload manifest") end' "$manifest" 2>/dev/null); then
    echo "::error::$manifest is not an upload manifest this action wrote"
    return 1
  fi
  recorded_id=$(head -n 1 <<< "$recorded")
  if [ "${recorded_id,,}" != "${DEPLOYMENT_ID,,}" ]; then
    echo "::error::$manifest records deployment ${recorded_id//[$'\r\n']/ }, not $DEPLOYMENT_ID: m2repo-path is not the m2repo staged as this deployment"
    problems=1
  fi
  while IFS= read -r line; do
    echo "::error::$line"
    problems=$((problems + 1))
  done < <(awk -F'\t' -v id="$DEPLOYMENT_ID" -v m2repo="$M2REPO" '
      FILENAME == ARGV[1] { recorded[$1] = $2; next }
      !($1 in recorded) { print "Deployment " id " lacks " $1 " (upload manifest), which " m2repo " holds"; next }
      recorded[$1] != $2 { print "SHA-256 mismatch for " $1 ": upload manifest " recorded[$1] ", m2repo " $2 }
      { delete recorded[$1] }
      END { for (path in recorded) print "Deployment " id " holds " path " (upload manifest), absent from " m2repo }
    ' <(tail -n +2 <<< "$recorded") "$EXPECTED" | LC_ALL=C sort)
  [ "$problems" -eq 0 ] || return 1
  echo "Upload manifest matches $M2REPO: $(wc -l < "$EXPECTED") file(s) of deployment $DEPLOYMENT_ID"
}

# verify_components: the deployment's purls must name exactly the
# components the m2repo holds; uses STATUS_BODY from the last status read.
verify_components() {
  local expected actual purl problems=0
  # Called under ||, where errexit is off, so failures return explicitly
  expected=$(list_component_purls "$M2REPO") || return 1
  if [ -z "$expected" ]; then
    echo "::error::No POMs found in '$M2REPO'"
    return 1
  fi
  # Compare on the base purl; qualifiers such as ?type=war name files
  # of a component, not further components.
  actual=$(jq -r '.purls[]? | strings' <<< "$STATUS_BODY" |
    sed 's/[?#].*//' | LC_ALL=C sort -u)
  while IFS= read -r purl; do
    [ -n "$purl" ] || continue
    echo "::error::Deployment $DEPLOYMENT_ID lacks component $purl"
    problems=$((problems + 1))
  done < <(LC_ALL=C comm -23 <(printf '%s\n' "$expected") <(printf '%s\n' "$actual"))
  while IFS= read -r purl; do
    [ -n "$purl" ] || continue
    echo "::error::Deployment $DEPLOYMENT_ID holds component $purl, absent from $M2REPO"
    problems=$((problems + 1))
  done < <(LC_ALL=C comm -13 <(printf '%s\n' "$expected") <(printf '%s\n' "$actual"))
  [ "$problems" -eq 0 ] || return 1
  echo "Deployment components match $M2REPO: $(wc -l <<< "$expected")"
}

# verify_files: download every file EXPECTED lists, .md5/.sha1 sidecars
# included, and compare its SHA-256 with the one the m2repo gives.
verify_files() {
  local file http remote expected problems=0 download
  download=$(mktemp)
  while IFS=$'\t' read -r file expected; do
    http=$(curl -sS -L --proto-redir =https --max-redirs 3 --retry 3 \
      --connect-timeout 30 --max-time 600 -o "$download" -w '%{http_code}' \
      -H "Authorization: Bearer $AUTH_TOKEN" \
      "${CENTRAL_URL}/api/v1/publisher/deployment/${DEPLOYMENT_ID}/download/$(uri_path "$file")") ||
      http="000"
    case "$http" in
      200)
        remote=$(sha256_of < "$download")
        if [ "$remote" = "$expected" ]; then
          VERIFIED=$((VERIFIED + 1))
        else
          echo "::error::SHA-256 mismatch for $file: deployment $remote, m2repo $expected"
          problems=$((problems + 1))
        fi
        ;;
      404)
        echo "::error::Deployment $DEPLOYMENT_ID lacks $file"
        problems=$((problems + 1))
        ;;
      *)
        echo "::error::Could not download $file from deployment $DEPLOYMENT_ID (HTTP $http)"
        problems=$((problems + 1))
        ;;
    esac
  done < "$EXPECTED"
  rm -f "$download"
  [ "$problems" -eq 0 ] || return 1
  echo "Downloaded files match $M2REPO: $VERIFIED (SHA-256, .md5/.sha1 sidecars included)"
}

# request_publication: ask the Portal to publish. A refusal is fine when
# the deployment turns out to be publishing already (a concurrent run).
request_publication() {
  local reply http
  reply=$(mktemp)
  http=$(curl -sS -X POST -o "$reply" -w '%{http_code}' \
    --connect-timeout 30 --max-time 120 \
    "${CENTRAL_URL}/api/v1/publisher/deployment/${DEPLOYMENT_ID}" \
    -H "Authorization: Bearer $AUTH_TOKEN") || http="000"
  if [[ "$http" =~ ^2[0-9][0-9]$ ]]; then
    rm -f "$reply"
    echo "Publication of deployment $DEPLOYMENT_ID requested (HTTP $http)"
    return 0
  fi
  echo "Publish request returned HTTP $http; reading the status again..."
  central_status "$DEPLOYMENT_ID" || true
  case "$DEPLOYMENT_STATUS" in
    PUBLISHING|PUBLISHED)
      rm -f "$reply"
      echo "::notice::Deployment $DEPLOYMENT_ID is $DEPLOYMENT_STATUS: another run published it"
      ;;
    *)
      echo "::error::Publish request for deployment $DEPLOYMENT_ID failed (HTTP $http). Response:"
      head -c 2000 "$reply" || true
      echo
      rm -f "$reply"
      return 1
      ;;
  esac
}

if [ "$INPUT_SKIP_VERIFICATION" = "true" ]; then
  VERIFY="false"
  echo "::warning::skip-verification is true: this run publishes deployment $DEPLOYMENT_ID WITHOUT checking its files or components against a staged m2repo"
elif [ -d "$M2REPO" ]; then
  VERIFY="true"
  EXPECTED=$(mktemp)
  list_bundle_digests "$M2REPO" > "$EXPECTED"
else
  echo "::error::Publish mode checks the deployment against the signed m2repo staged with it, but m2repo-path '$M2REPO' is not a directory. Restore that m2repo, or set skip-verification: true to publish unchecked."
  exit 1
fi

poll_deployment "$DEPLOYMENT_ID" SETTLED

case "$DEPLOYMENT_STATUS" in
  VALIDATED)
    if [ "$VERIFY" = "true" ]; then
      RESULT=0
      verify_manifest || RESULT=1
      verify_components || RESULT=1
      verify_files || RESULT=1
      if [ "$RESULT" -ne 0 ]; then
        echo "::error::Deployment $DEPLOYMENT_ID does not match $M2REPO; not publishing it"
        exit 1
      fi
    fi
    if [ "$INPUT_DRY_RUN" = "true" ]; then
      echo "::notice::dry-run: deployment $DEPLOYMENT_ID checked and left unpublished"
      exit 0
    fi
    request_publication
    ;;
  PUBLISHING|PUBLISHED)
    echo "::notice::Deployment $DEPLOYMENT_ID is already $DEPLOYMENT_STATUS; not publishing it again"
    if [ "$VERIFY" = "true" ]; then
      RESULT=0
      verify_manifest || RESULT=1
      verify_components || RESULT=1
      if [ "$RESULT" -ne 0 ]; then
        echo "::error::Deployment $DEPLOYMENT_ID does not match $M2REPO"
        exit 1
      fi
      echo "::notice::Skipping the file check: the Portal documents its download endpoint for deployments awaiting publication, and the run that published this one checked its files first"
    fi
    if [ "$INPUT_DRY_RUN" = "true" ]; then
      exit 0
    fi
    ;;
  *)
    echo "::error::Deployment $DEPLOYMENT_ID is in state '$DEPLOYMENT_STATUS', which this action cannot publish"
    exit 1
    ;;
esac

if [ "$DEPLOYMENT_STATUS" != "PUBLISHED" ]; then
  poll_deployment "$DEPLOYMENT_ID" PUBLISHED
fi
echo "✅ Deployment $DEPLOYMENT_ID PUBLISHED"
