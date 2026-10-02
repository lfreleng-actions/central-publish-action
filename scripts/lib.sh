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
