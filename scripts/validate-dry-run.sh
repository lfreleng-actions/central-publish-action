#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

# Validate the dry-run input and publish it as the dry-run step output,
# the one value every later step reads. Anything other than exactly
# 'true' or 'false' fails, so a typo can never upload or publish.
set -euo pipefail

case "${INPUT_DRY_RUN-}" in
  true|false)
    echo "dry-run=$INPUT_DRY_RUN" >> "$GITHUB_OUTPUT"
    ;;
  *)
    echo "::error::dry-run '${INPUT_DRY_RUN-}' must be 'true' or 'false'"
    exit 1
    ;;
esac
