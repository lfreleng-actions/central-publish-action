<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# Central Publish Action

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/central-publish-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lfreleng-actions/central-publish-action/badge)](https://scorecard.dev/viewer/?uri=github.com/lfreleng-actions/central-publish-action)
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
  checking it holds the staged files and nothing else
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

`dry-run` must be `true` or `false`. Any other value, including an empty
string, `TRUE` or a stray space, fails input validation, before any
signing, upload or publication.

### Stage now, release later (`mode: publish`)

A release lane can upload in the default `USER_MANAGED` mode at stage time,
record `deployment_id`, keep the signed m2repo, and publish that deployment
weeks later. Save the m2repo after the upload step, so it holds the upload
manifest (see below):

```yaml
# Release workflow
- uses: lfreleng-actions/central-publish-action@main
  with:
    mode: publish
    deployment-id: ${{ needs.load-stage-record.outputs.deployment-id }}
    m2repo-path: m2repo # saved after upload, with central-deployment.json
    central-username: ${{ secrets.CENTRAL_USERNAME }}
    central-token: ${{ secrets.CENTRAL_TOKEN }}
```

Publish mode signs, bundles and uploads nothing. It reads the deployment's
status, waiting up to `poll-timeout` through `PENDING` and `VALIDATING`,
then acts on the state:

| State                     | Action                                                                                   |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `VALIDATED`               | Check the deployment against `m2repo-path`, publish it, wait for `PUBLISHED`             |
| `PUBLISHING`, `PUBLISHED` | Check manifest and components, skip downloads, wait for `PUBLISHED`; no second publish   |
| `FAILED`                  | Fail, printing the Portal's errors                                                       |
| `PENDING`, `VALIDATING`   | Fail once `poll-timeout` expires                                                         |
| Any other state           | Fail                                                                                     |

The check stops a stale or mistyped `deployment-id` from releasing the wrong
artifacts. Publish mode publishes when the deployment, its upload
manifest and `m2repo-path` hold the same set of files with the same
bytes, and never otherwise. A file missing from either side, an extra file on either side or a
digest mismatch fails the run before any publish request.

#### The upload manifest

This action takes a deployment's complete file inventory from a
manifest it records at upload time, rather than from the Portal. It
then fetches each recorded file through the Portal's
[download endpoint](https://central.sonatype.org/publish/publish-portal-api/#manually-testing-a-deployment-bundle)
by path and compares its digest. Once an upload succeeds, upload mode writes
`central-deployment.json` at the root of `m2repo-path` (output
`manifest_path`). It records the deployment ID the Portal returned and the
SHA-256 of every file the uploaded bundle held, `.md5`/`.sha1` sidecars
included, read from the uploaded ZIP rather than from `m2repo-path`, which
may have changed since bundling. Bundles never include the manifest. Before publishing, the action:

- fails unless the manifest exists and records this `deployment-id`;
- fails unless the manifest lists the same set of files a bundle built
  from `m2repo-path` would carry, with the same SHA-256 each: every
  artifact and `.asc` signature unchanged, and the `.md5`/`.sha1` sidecars
  as the bundle step generates them from those files. Publish mode derives the sidecars
  rather than reading them, as `m2repo-path` may hold stale ones or none.
  Each sidecar holds the bare hex digest of its file's bytes, so restoring
  the m2repo under a different path does not change it.
  An orphan `.md5` or `.sha1` file, one with no artifact or signature
  beside it, travels in the bundle unchanged, so publish mode counts it
  with its own SHA-256;
- compares the deployment's `purls` with the components in `m2repo-path`
  (one per POM, from the `group/artifactId/version` layout) and fails on any
  component missing from either side;
- downloads every one of those files, sidecars included, from the
  deployment and fails unless each SHA-256 matches.

**Trust model.** The download check proves the deployment holds every
expected file with the expected bytes. The manifest is what rules out
extra files: a Portal deployment holds the one bundle uploaded to create it, and
the manifest records that bundle's contents under the ID the upload
returned. The guarantee rests on the manifest being the one upload mode
wrote, and the manifest is as trustworthy as the store the caller
restores `m2repo-path` from; anyone who can alter the saved m2repo can alter both.
The manifest has a name that does not start with a dot, so
`actions/upload-artifact`, which skips hidden files by default, keeps it.

The Portal documents its download endpoint for validated deployments
awaiting publication. A re-run that finds the deployment already
`PUBLISHING` or `PUBLISHED` checks the manifest and components, skips the
downloads, says so in the log, and relies on the run that published it,
which downloaded every file first. Re-running a release is safe: it never
publishes twice, and it succeeds once the deployment is `PUBLISHED`.

Publish mode needs `m2repo-path` to exist and to hold the upload manifest;
a deployment staged without one fails the check. Setting
`skip-verification: true` publishes without any check and logs a warning;
keep it for emergencies. With `dry-run: true`, publish mode runs the checks
and stops before publishing.

## Inputs

<!-- markdownlint-disable MD013 -->

| Input               | Required | Default                        | Description                                                                         |
| ------------------- | -------- | ------------------------------ | ----------------------------------------------------------------------------------- |
| `mode`              | no       | `upload`                       | `upload` (sign, bundle, upload) or `publish` (release an existing deployment)       |
| `deployment-id`     | cond.    | _(empty)_                      | Deployment to publish; required with `mode: publish`, rejected in upload mode       |
| `skip-verification` | no       | `false`                        | Publish mode: `true` publishes without checking against `m2repo-path`               |
| `m2repo-path`       | yes      | `m2repo`                       | Path to local Maven repo directory; in publish mode, the m2repo saved after upload  |
| `central-username`  | yes      | —                              | Central Portal token username                                                       |
| `central-token`     | yes      | —                              | Central Portal token password                                                       |
| `central-url`       | no       | `https://central.sonatype.com` | Portal base URL: `https://host[:port]`, or loopback `http` for a test mock          |
| `signing-method`    | no       | `gpg`                          | Upload mode: `gpg`, `sigul`, or `none` (see below)                                  |
| `gpg-private-key`   | cond.    | —                              | GPG private key (base64-encoded armor); required when `signing-method=gpg`          |
| `gpg-passphrase`    | no       | _(empty)_                      | GPG key passphrase                                                                  |
| `publishing-type`   | no       | `USER_MANAGED`                 | Upload mode: `AUTOMATIC` or `USER_MANAGED`                                          |
| `dry-run`           | no       | `false`                        | `true` or `false`; `true` skips upload (upload mode) or publishing (publish mode)   |
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
| `verified_file_count` | Publish mode: downloaded files whose SHA-256 matched (0 if not checked)     |
| `bundle_path`         | Path to the created bundle ZIP (upload mode)                                |
| `manifest_path`       | Upload manifest written into `m2repo-path` after a successful upload        |

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
5. Write the upload manifest of bundle.zip: <m2repo>/central-deployment.json
6. Poll /api/v1/publisher/status until VALIDATED/PUBLISHED/FAILED
```

Publish mode:

```text
1. Poll /api/v1/publisher/status?id=<deployment-id> until it settles
2. VALIDATED: match the upload manifest with the m2repo, compare purls,
   download and hash each file from
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
the scripts against it; it needs Python 3.12 or later, bash, curl, jq, zip, unzip,
GNU coreutils and gpg:

```sh
python3 -m unittest discover -s tests -t . -v
```

The `Mock Portal Tests` CI job runs that suite, then stages and releases a
deployment through the action itself against the mock. It needs no secrets.

The `Build and Publish Test` CI job builds a small Maven project with a fresh
`0.0.0-ci.<run id>.<run attempt>` version on every run. On a push to `main`,
on a weekly schedule against `main` (Mondays, 04:37 UTC) and on a pull
request from this repository, it stages that project on the real Central
Portal as a `USER_MANAGED` deployment, then drops it through the
Publisher API once validation settles, whether the run passed or failed.
The drop step leaves any `PUBLISHING` or `PUBLISHED` deployment alone, and
fails the job, naming the deployment, when it cannot drop a `VALIDATED` or
`FAILED` one. Both CI jobs run on every pull request to `main`, whatever
it changes: the default branch ruleset requires `Build and Publish Test`,
so a path filter on the `pull_request` trigger would leave pull requests
outside it unable to merge. A push to `main` runs them when it changes
the action, `scripts/`, `tests/` or the workflow, and not otherwise. A
manual run (`workflow_dispatch`) defaults to a dry run that uploads
nothing; with `dry-run` cleared it stages a `USER_MANAGED` deployment and
keeps it for inspection, logging its ID: drop it in the Portal afterwards.
With `publishing-type: AUTOMATIC` as well, a manual run publishes the
artefact to Maven Central, which nobody can undo. The job reads this
configuration, at organisation or repository level:

| Name                     | Kind     | Purpose                                         |
| ------------------------ | -------- | ----------------------------------------------- |
| `CENTRAL_USERNAME`       | variable | Central Portal user token name                  |
| `CENTRAL_TOKEN`          | secret   | Central Portal user token password              |
| `GPG_PRIVATE_KEY_B64`    | secret   | Base64-encoded GPG private key for signing      |
| `GPG_PASSPHRASE`         | secret   | Optional; passphrase for that key               |
| `CENTRAL_TEST_NAMESPACE` | variable | Verified Central namespace, used as the groupId |

A run from this repository fails if any of these, bar `GPG_PASSPHRASE`, is
missing. Fork and Dependabot pull requests receive no secrets, so they run
an unsigned dry run instead, even when the variables are available, with
`org.example` as the groupId when `CENTRAL_TEST_NAMESPACE` is unavailable
too. A scheduled run always counts as a run from this repository.

A new push to a pull request cancels that pull request's earlier run. No
other run cancels or waits for another, so each merge to `main` gets its
own result.

## License

Apache-2.0
