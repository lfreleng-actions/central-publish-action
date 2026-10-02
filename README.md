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

## Inputs

| Input              | Required | Default        | Description                                                                |
|--------------------|----------|----------------|----------------------------------------------------------------------------|
| `m2repo-path`      | yes      | `m2repo`       | Path to local Maven repo directory                                         |
| `central-username` | yes      | —              | Central Portal token username                                              |
| `central-token`    | yes      | —              | Central Portal token password                                              |
| `signing-method`   | no       | `gpg`          | `gpg`, `sigul`, or `none` (see below)                                      |
| `gpg-private-key`  | cond.    | —              | GPG private key (base64-encoded armor); required when `signing-method=gpg` |
| `gpg-passphrase`   | no       | _(empty)_      | GPG key passphrase                                                         |
| `publishing-type`  | no       | `USER_MANAGED` | `AUTOMATIC` or `USER_MANAGED`                                              |
| `dry-run`          | no       | `false`        | Skip upload, create bundle file                                            |
| `poll-timeout`     | no       | `600`          | Max seconds to wait for validation                                         |
| `poll-interval`    | no       | `15`           | Seconds between status polls                                               |

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

| Output              | Description                                              |
|---------------------|----------------------------------------------------------|
| `deployment_id`     | Central Portal deployment ID                             |
| `deployment_status` | Final status: `VALIDATED`, `PUBLISHED`, or `FAILED`      |
| `bundle_path`       | Path to the created bundle ZIP                           |

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

## Testing

Use `publishing-type: USER_MANAGED` for safe testing:

- The action uploads and validates artifacts
- NOT published to Maven Central
- Maintainers can delete it from the Portal UI
- Perfect for CI testing

## License

Apache-2.0

[pre-commit.ci results page]: https://results.pre-commit.ci/latest/github/lfreleng-actions/central-publish-action/main
[pre-commit.ci status badge]: https://results.pre-commit.ci/badge/github/lfreleng-actions/central-publish-action/main.svg
