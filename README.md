<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Central Publish Action

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/central-publish-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![pre-commit.ci status badge]][pre-commit.ci results page] [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lfreleng-actions/central-publish-action/badge)](https://scorecard.dev/viewer/?uri=github.com/lfreleng-actions/central-publish-action)
<!-- prettier-ignore-end -->

Publish Maven artifacts to Maven Central via the [Central Portal REST API](https://central.sonatype.com/publishing).

## Features

- GPG signs every artifact in the bundle (`.jar`, `.pom`, `.module`, `.war`,
  `.zip`, attached `.xml` files and so on)
- Creates compliant bundle ZIP for Central Portal upload
- Supports `AUTOMATIC` (auto-publish) and `USER_MANAGED` (validation) modes
- Polls deployment status until completion
- `dry-run` mode for local testing (creates bundle, skips upload)
- `publish` mode: releases a deployment staged earlier, by ID, after
  checking it still holds the staged files
- Generates `$GITHUB_STEP_SUMMARY` with results

## Usage

### Basic (validation — safe for testing)

```yaml
- uses: lfreleng-actions/central-publish-action@main
  with:
    m2repo-path: m2repo
    central-username: ${{ secrets.CENTRAL_USERNAME }}
    central-token: ${{ secrets.CENTRAL_TOKEN }}
    gpg-private-key: ${{ secrets.GPG_PRIVATE_KEY_B64 }}
    publishing-type: USER_MANAGED
```

### Production (auto-publish)

```yaml
- uses: lfreleng-actions/central-publish-action@main
  with:
    m2repo-path: m2repo
    central-username: ${{ secrets.CENTRAL_USERNAME }}
    central-token: ${{ secrets.CENTRAL_TOKEN }}
    gpg-private-key: ${{ secrets.GPG_PRIVATE_KEY_B64 }}
    publishing-type: AUTOMATIC
```

With `AUTOMATIC`, the action succeeds once Central reports the deployment
`PUBLISHED`; a deployment still `VALIDATED` or `PUBLISHING` when
`poll-timeout` expires fails the step. Raise `poll-timeout` for large
releases.

### Dry run (no upload)

```yaml
- uses: lfreleng-actions/central-publish-action@main
  with:
    m2repo-path: m2repo
    central-username: ${{ secrets.CENTRAL_USERNAME }}
    central-token: ${{ secrets.CENTRAL_TOKEN }}
    gpg-private-key: ${{ secrets.GPG_PRIVATE_KEY_B64 }}
    dry-run: "true"
```

### Stage now, release later (`mode: publish`)

A release lane can upload in the default `USER_MANAGED` mode at stage time,
record `deployment_id`, keep the signed m2repo, and publish that deployment
weeks later:

```yaml
# Release workflow
- uses: lfreleng-actions/central-publish-action@main
  with:
    mode: publish
    deployment-id: ${{ needs.load-stage-record.outputs.deployment-id }}
    m2repo-path: m2repo # the signed m2repo saved at stage time
    central-username: ${{ secrets.CENTRAL_USERNAME }}
    central-token: ${{ secrets.CENTRAL_TOKEN }}
```

Publish mode signs, bundles and uploads nothing. It reads the deployment's
status, waiting up to `poll-timeout` through `PENDING` and `VALIDATING`,
then acts on the state:

| State                     | Action                                                                                   |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `VALIDATED`               | Check the deployment against `m2repo-path`, publish it, wait for `PUBLISHED`             |
| `PUBLISHING`, `PUBLISHED` | Check its components, skip the file check, wait for `PUBLISHED`; no second publish call  |
| `FAILED`                  | Fail, printing the Portal's errors                                                       |
| `PENDING`, `VALIDATING`   | Fail once `poll-timeout` expires                                                         |
| Any other state           | Fail                                                                                     |

The check stops a stale or mistyped `deployment-id` from releasing the wrong
artifacts. Before publishing, the action:

- compares the deployment's `purls` with the components in `m2repo-path`
  (one per POM, from the `group/artifactId/version` layout) and fails on any
  component missing from either side;
- downloads every artifact and `.asc` signature the bundle carried from the
  Portal's
  [deployment download endpoint](https://central.sonatype.org/publish/publish-portal-api/#manually-testing-a-deployment-bundle)
  and fails unless each SHA-256 matches the m2repo copy. It leaves out the
  `.md5`/`.sha1` sidecars: the action generates them from those same files,
  and Central checks them during validation.

The Portal documents that endpoint for validated deployments awaiting
publication. A re-run that finds the deployment already `PUBLISHING` or
`PUBLISHED` skips the file check, says so in the log, and relies on
the run that published it, which checked first. Re-running a release is safe:
it never publishes twice, and it succeeds once the deployment is `PUBLISHED`.

Publish mode needs `m2repo-path` to exist. Setting `skip-verification: true`
publishes without any check and logs a warning; keep it for emergencies.
With `dry-run: true`, publish mode runs the checks and stops before
publishing.

## Inputs

<!-- markdownlint-disable MD013 -->

| Input               | Required | Default                        | Description                                                                         |
| ------------------- | -------- | ------------------------------ | ----------------------------------------------------------------------------------- |
| `mode`              | no       | `upload`                       | `upload` (sign, bundle, upload) or `publish` (release an existing deployment)       |
| `deployment-id`     | cond.    | _(empty)_                      | Deployment to publish; required with `mode: publish`, rejected in upload mode       |
| `skip-verification` | no       | `false`                        | Publish mode: `true` publishes without checking against `m2repo-path`               |
| `m2repo-path`       | yes      | `m2repo`                       | Path to local Maven repo directory; in publish mode, the signed m2repo staged       |
| `central-username`  | yes      | —                              | Central Portal token username                                                       |
| `central-token`     | yes      | —                              | Central Portal token password                                                       |
| `central-url`       | no       | `https://central.sonatype.com` | Portal base URL: `https://host[:port]`, or loopback `http` for a test mock          |
| `signing-method`    | no       | `gpg`                          | Upload mode: `gpg`, `sigul`, or `none` (see below)                                  |
| `gpg-private-key`   | cond.    | —                              | GPG private key (base64-encoded armor); required when `signing-method=gpg`          |
| `gpg-passphrase`    | no       | _(empty)_                      | GPG key passphrase                                                                  |
| `publishing-type`   | no       | `USER_MANAGED`                 | Upload mode: `AUTOMATIC` or `USER_MANAGED`                                          |
| `dry-run`           | no       | `false`                        | Upload mode: create the bundle, skip upload. Publish mode: check, skip publishing   |
| `poll-timeout`      | no       | `600`                          | Max seconds to wait for each phase (validation, then publication)                   |
| `poll-interval`     | no       | `15`                           | Seconds between status polls                                                        |

<!-- markdownlint-enable MD013 -->

### Signing methods

Maven Central requires a detached ASCII-armored `.asc` signature for every
deployable artifact. The `signing-method` input controls how the action creates them:

- **`gpg`** (default) — the action imports `gpg-private-key` and signs every
  file the bundle carries other than checksums and signatures (leaving files
  that already carry a `.asc` intact).
- **`sigul`** — the caller MUST pre-sign the artifacts in a prior step (e.g.
  [`lfit/sigul-sign-action`](https://github.com/lfit/sigul-sign-action)). The
  action verifies that a `.asc` exists for every deployable artifact and
  fails if any is missing. `gpg-private-key` is not used.
- **`none`** — no signing and no verification. Intended for `dry-run` or
  non-Central testing; Maven Central rejects an unsigned bundle.

The bundle holds every file under `m2repo-path` except `maven-metadata.xml*`,
`_remote.repositories`, `*.sha256` and `*.sha512`, at any depth, plus the
`.md5` and `.sha1` checksums the action generates. Signing and the `sigul`
check cover that same set, less checksums and `.asc` files.

## Outputs

| Output                | Description                                                                 |
| --------------------- | --------------------------------------------------------------------------- |
| `deployment_id`       | Central Portal deployment ID                                                |
| `deployment_status`   | Last state seen: `VALIDATED`, `PUBLISHING`, `PUBLISHED`, `FAILED`, …        |
| `verified_file_count` | Publish mode: files whose SHA-256 matched `m2repo-path` (0 if not checked)  |
| `bundle_path`         | Path to the created bundle ZIP (upload mode)                                |

## Requirements

### Maven Central POM Compliance

Your POM must include:

- `<name>` — project name
- `<description>` — project description
- `<url>` — project URL
- `<licenses>` — at least one license
- `<developers>` — at least one developer
- `<scm>` — source control info

### Artifacts Required

For each module:

- `*.jar` — compiled artifact
- `*.pom` — POM file
- `*-sources.jar` — source code
- `*-javadoc.jar` — Javadoc

The action generates `.asc` (GPG signature) for each file automatically.

## How It Works

```text
1. Import GPG key from base64 secret
2. Sign each bundled file (other than checksums) → .asc signatures
3. Create bundle.zip (Maven directory structure, generated .md5/.sha1)
4. POST bundle to Central Portal: /api/v1/publisher/upload
5. Poll /api/v1/publisher/status until VALIDATED/PUBLISHED/FAILED
```

Publish mode:

```text
1. Poll /api/v1/publisher/status?id=<deployment-id> until it settles
2. VALIDATED: compare purls, download and hash each file from
   /api/v1/publisher/deployment/<id>/download/<path>
3. POST /api/v1/publisher/deployment/<id> to publish
4. Poll until PUBLISHED
```

## Testing

Use `publishing-type: USER_MANAGED` for safe testing:

- The action uploads and validates artifacts
- NOT published to Maven Central
- Maintainers can delete it from the Portal UI
- Perfect for CI testing

The step logic lives in `scripts/`. `tests/` holds a mock Central Portal
(`mock_central.py`, Python standard library alone) and a suite that drives
the scripts against it; it needs Python 3.12 or later, bash, curl, jq, zip,
GNU coreutils and gpg:

```sh
python3 -m unittest discover -s tests -t . -v
```

The `Mock Portal Tests` CI job runs that suite, then stages and releases a
deployment through the action itself against the mock. It needs no secrets.

## License

Apache-2.0

[pre-commit.ci results page]: https://results.pre-commit.ci/latest/github/lfreleng-actions/central-publish-action/main
[pre-commit.ci status badge]: https://results.pre-commit.ci/badge/github/lfreleng-actions/central-publish-action/main.svg
