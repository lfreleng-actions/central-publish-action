#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Functions shared by the action's steps; source this file, don't run it.

# The upload manifest, at the root of the m2repo: the deployment ID the
# Portal returned and the SHA-256 of every file the uploaded bundle held.
MANIFEST_NAME="central-deployment.json"

# list_bundle_files DIR: print, relative to DIR and sorted, every file the
# Central bundle carries. This is the one definition of that set: the
# checksum, signing, signature-check and deployment-check steps all
# derive theirs from it. Repository bookkeeping stays out at any depth,
# and the upload manifest stays out at the root.
list_bundle_files() {
  (cd "$1" && find . -type f ! -path "./$MANIFEST_NAME" \
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

# sha256_of: print the SHA-256 of standard input.
sha256_of() {
  sha256sum | awk '{print $1}'
}

# sidecar md5|sha1: print the content of the .md5 or .sha1 sidecar for
# standard input, the bare hex digest. Hashing standard input, never a
# path, keeps md5sum and sha1sum from prefixing a backslash when the path
# holds one, so the sidecar depends on the file's bytes alone.
sidecar() {
  "${1}sum" | awk '{print $1}'
}

# write_sidecars DIR: write the .md5 and .sha1 sidecar the Central Portal
# requires beside each payload file in DIR.
write_sidecars() {
  local file
  while IFS= read -r file; do
    sidecar md5 < "$1/$file" > "$1/${file}.md5"
    sidecar sha1 < "$1/$file" > "$1/${file}.sha1"
  done < <(list_payload_files "$1")
}

# list_bundle_digests DIR: print "<path><TAB><SHA-256>" for every file a
# bundle built from DIR carries: each payload file, the .md5 and .sha1
# sidecars the bundle step derives from it, and any orphan .md5 or .sha1
# file, one with no payload file beside it, which the bundle carries as
# it is. Derived sidecars' digests come from the payload, not from DIR,
# which may hold none or stale ones. They hash the bytes write_sidecars
# writes, from the same sidecar function.
list_bundle_digests() {
  local file
  local -A payload=()
  while IFS= read -r file; do
    payload["$file"]=1
    printf '%s\t%s\n' "$file" "$(sha256_of < "$1/$file")"
    printf '%s.md5\t%s\n' "$file" "$(sidecar md5 < "$1/$file" | sha256_of)"
    printf '%s.sha1\t%s\n' "$file" "$(sidecar sha1 < "$1/$file" | sha256_of)"
  done < <(list_payload_files "$1")
  while IFS= read -r file; do
    [ -z "${payload["${file%.*}"]+set}" ] || continue
    printf '%s\t%s\n' "$file" "$(sha256_of < "$1/$file")"
  done < <(list_bundle_files "$1" | awk '/\.(md5|sha1)$/')
}

# list_component_purls DIR: print, sorted, the Maven package URL of each
# component in DIR, one per POM, from the repository layout
# <group path>/<artifactId>/<version>/<artifactId>-<version>*.pom.
list_component_purls() {
  list_payload_files "$1" | awk -F/ '
    /\.pom$/ {
      if (NF < 4 || index($NF, $(NF - 2) "-" $(NF - 1)) != 1) {
        print "::error::" $0 " does not follow the Maven repository layout"
        bad = 1
        next
      }
      group = $1
      for (i = 2; i <= NF - 3; i++) group = group "." $i
      print "pkg:maven/" group "/" $(NF - 2) "@" $(NF - 1)
    }
    END { exit bad }' | LC_ALL=C sort -u
}

# uri_path PATH: percent-encode each segment of a relative path.
uri_path() {
  jq -rn --arg path "$1" '$path | split("/") | map(@uri) | join("/")'
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

# check_central_url URL: the default Portal, any other https://host[:port],
# or plain http to a loopback address (for a local mock). No path, query
# or credentials.
check_central_url() {
  if [[ ! "$1" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] &&
     [[ ! "$1" =~ ^http://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?$ ]]; then
    echo "::error::central-url '$1' must be https://host[:port] or a loopback http://host[:port]"
    return 1
  fi
}

# central_init: check CENTRAL_URL and set AUTH_TOKEN for the Portal API.
central_init() {
  CENTRAL_URL="${CENTRAL_URL%/}"
  check_central_url "$CENTRAL_URL"
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
