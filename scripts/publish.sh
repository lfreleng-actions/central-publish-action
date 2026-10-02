#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Publish an existing deployment by ID (mode=publish), after checking it
# holds the components and bytes of the signed m2repo staged earlier.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "$0")/lib.sh"

DEPLOYMENT_ID="$INPUT_DEPLOYMENT_ID"
M2REPO="$INPUT_M2REPO"
DEPLOYMENT_STATUS="UNKNOWN"
VERIFIED=0

require_deployment_id "$DEPLOYMENT_ID"
central_init
echo "deployment_id=$DEPLOYMENT_ID" >> "$GITHUB_OUTPUT"
trap 'echo "deployment_status=$DEPLOYMENT_STATUS" >> "$GITHUB_OUTPUT"
      echo "verified_file_count=$VERIFIED" >> "$GITHUB_OUTPUT"' EXIT

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

# verify_files: download each artefact and signature the bundle carried
# and compare its SHA-256 with the m2repo copy. The .md5/.sha1 sidecars
# are left out: the bundle step derives them from these same files, and
# Central checked them against those files during validation.
verify_files() {
  local file http remote expected problems=0 download
  download=$(mktemp)
  while IFS= read -r file; do
    http=$(curl -sS -L --proto-redir =https --max-redirs 3 --retry 3 \
      --connect-timeout 30 --max-time 600 -o "$download" -w '%{http_code}' \
      -H "Authorization: Bearer $AUTH_TOKEN" \
      "${CENTRAL_URL}/api/v1/publisher/deployment/${DEPLOYMENT_ID}/download/$(uri_path "$file")") ||
      http="000"
    case "$http" in
      200)
        remote=$(sha256sum < "$download" | awk '{print $1}')
        expected=$(sha256sum < "$M2REPO/$file" | awk '{print $1}')
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
  done < <(list_payload_files "$M2REPO")
  rm -f "$download"
  [ "$problems" -eq 0 ] || return 1
  echo "Deployment files match $M2REPO: $VERIFIED (SHA-256)"
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
else
  echo "::error::Publish mode checks the deployment against the signed m2repo staged with it, but m2repo-path '$M2REPO' is not a directory. Restore that m2repo, or set skip-verification: true to publish unchecked."
  exit 1
fi

poll_deployment "$DEPLOYMENT_ID" SETTLED

case "$DEPLOYMENT_STATUS" in
  VALIDATED)
    if [ "$VERIFY" = "true" ]; then
      RESULT=0
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
      verify_components
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
