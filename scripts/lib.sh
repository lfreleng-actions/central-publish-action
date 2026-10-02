#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Functions shared by the action's steps; source this file, don't run it.

# list_bundle_files DIR: print, relative to DIR and sorted, every file the
# Central bundle carries. This is the one definition of that set: the
# checksum, signing, signature-check and deployment-check steps all
# derive theirs from it. Repository bookkeeping stays out at any depth.
list_bundle_files() {
  (cd "$1" && find . -type f \
    ! -name 'maven-metadata.xml*' ! -name '_remote.repositories' \
    ! -name '*.sha256' ! -name '*.sha512' -print) |
    sed 's|^\./||' | LC_ALL=C sort
}

# list_payload_files DIR: the bundle minus the .md5/.sha1 sidecars the
# bundle step generates; artefacts and their .asc signatures.
list_payload_files() {
  list_bundle_files "$1" | awk '!/\.(md5|sha1)$/'
}

# list_signable_files DIR: every payload file that needs a .asc beside it.
list_signable_files() {
  list_payload_files "$1" | awk '!/\.asc$/'
}

# Central Portal deployment IDs are UUIDs; anything else is refused before
# it reaches a URL or GITHUB_OUTPUT.
DEPLOYMENT_ID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

# require_deployment_id ID: fail unless ID has the Portal's UUID format.
require_deployment_id() {
  if [[ ! "$1" =~ $DEPLOYMENT_ID_RE ]]; then
    echo "::error::deployment-id '${1//[$'\r\n']/ }' is not a Central Portal deployment ID (UUID)"
    return 1
  fi
}

# central_init: check CENTRAL_URL and set AUTH_TOKEN for the Portal API.
# Non-default URLs must use https, or plain http to a loopback address
# (for a local mock), with no path, query or credentials.
central_init() {
  CENTRAL_URL="${CENTRAL_URL%/}"
  if [[ ! "$CENTRAL_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] &&
     [[ ! "$CENTRAL_URL" =~ ^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?$ ]]; then
    echo "::error::central-url '$CENTRAL_URL' must be https://host[:port] or a loopback http://host[:port]"
    return 1
  fi
  AUTH_TOKEN=$(printf '%s:%s' "$CENTRAL_USERNAME" "$CENTRAL_TOKEN" | base64 -w0)
  echo "::add-mask::$AUTH_TOKEN"
}

# central_status ID [MAX_SECONDS]: read the deployment's status once,
# giving up after MAX_SECONDS (default and ceiling 120). Sets STATUS_BODY
# to the reply and DEPLOYMENT_STATUS to its state; returns 1 on an HTTP
# error, leaving DEPLOYMENT_STATUS unchanged.
central_status() {
  local reply http max_time="${2:-120}"
  [ "$max_time" -le 120 ] || max_time=120
  reply=$(mktemp)
  http=$(curl -sS -X POST -o "$reply" -w '%{http_code}' \
    --connect-timeout 30 --max-time "$max_time" \
    "${CENTRAL_URL}/api/v1/publisher/status?id=$1" \
    -H "Authorization: Bearer $AUTH_TOKEN") || http="000"
  STATUS_BODY=$(cat "$reply")
  rm -f "$reply"
  if [ "$http" != "200" ]; then
    echo "  Status request failed (HTTP $http)"
    return 1
  fi
  DEPLOYMENT_STATUS=$(jq -r '.deploymentState // .state // "UNKNOWN"' \
    <<< "$STATUS_BODY" 2>/dev/null) || DEPLOYMENT_STATUS="UNKNOWN"
  # The state lands in GITHUB_OUTPUT, so it must be one plain word
  [[ "$DEPLOYMENT_STATUS" =~ ^[A-Z_]{1,32}$ ]] || DEPLOYMENT_STATUS="UNKNOWN"
}

# poll_deployment ID TARGET: read the status every INPUT_POLL_INTERVAL
# seconds for up to INPUT_POLL_TIMEOUT seconds, until TARGET holds:
#   VALIDATED  the deployment is VALIDATED or PUBLISHED
#   PUBLISHED  the deployment is PUBLISHED
#   SETTLED    the deployment has left PENDING and VALIDATING
# FAILED and the timeout return 1. The timeout is wall-clock time: each
# request and sleep gets no more than the time left. DEPLOYMENT_STATUS
# holds the last state seen, or UNKNOWN when no read succeeded.
poll_deployment() {
  local id="$1" target="$2" start remaining
  local timeout="$INPUT_POLL_TIMEOUT" interval="$INPUT_POLL_INTERVAL"
  start=$(date +%s)
  DEPLOYMENT_STATUS="UNKNOWN"
  echo "Polling deployment status (until $target, timeout: ${timeout}s, interval: ${interval}s)..."
  while remaining=$((start + timeout - $(date +%s))); [ "$remaining" -gt 0 ]; do
    if central_status "$id" "$remaining"; then
      echo "  [$(($(date +%s) - start))s] Status: $DEPLOYMENT_STATUS"
      case "$DEPLOYMENT_STATUS" in
        PUBLISHED)
          return 0
          ;;
        FAILED)
          echo "::error::Deployment $id FAILED"
          jq '.errors // .' <<< "$STATUS_BODY" 2>/dev/null || echo "$STATUS_BODY"
          return 1
          ;;
        VALIDATED)
          [ "$target" = "PUBLISHED" ] || return 0
          ;;
        PENDING|VALIDATING)
          ;;
        *)
          # PUBLISHING, or a state this action does not know
          [ "$target" != "SETTLED" ] || return 0
          ;;
      esac
    fi
    remaining=$((start + timeout - $(date +%s)))
    [ "$remaining" -gt 0 ] || break
    sleep "$((remaining < interval ? remaining : interval))"
  done
  echo "::error::Timed out after ${timeout}s waiting for deployment $id to reach $target (last status: $DEPLOYMENT_STATUS)"
  return 1
}
