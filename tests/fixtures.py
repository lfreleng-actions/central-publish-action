# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Build a small Maven repository layout for the action's tests.

Run as ``python3 tests/fixtures.py <dir>`` to create an unsigned copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

GROUP = "org/example"
VERSION = "1.0.0"

# Files the bundle carries and the signing steps cover, by relative path.
ARTEFACTS: tuple[str, ...] = (
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}.pom",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}.jar",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}-sources.jar",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}.war",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}-dist.zip",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}-dist.tar.gz",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}-cyclonedx.xml",
    f"{GROUP}/demo-parent/{VERSION}/demo-parent-{VERSION}.pom",
)

# Repository bookkeeping that never belongs in a Central bundle.
BOOKKEEPING: tuple[str, ...] = (
    f"{GROUP}/demo/maven-metadata.xml",
    f"{GROUP}/demo/maven-metadata.xml.md5",
    f"{GROUP}/demo/{VERSION}/_remote.repositories",
    f"{GROUP}/demo/{VERSION}/demo-{VERSION}.jar.sha256",
)

PURLS: tuple[str, ...] = (
    f"pkg:maven/org.example/demo-parent@{VERSION}",
    f"pkg:maven/org.example/demo@{VERSION}",
)


def build_m2repo(root: Path, signed: bool = False) -> None:
    """Populate ``root`` with artefacts and bookkeeping files.

    With ``signed``, every artefact gets a placeholder ``.asc`` beside it.
    """
    for relative in ARTEFACTS + BOOKKEEPING:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(f"content of {relative}\n".encode())
        if signed and relative in ARTEFACTS:
            _ = path.with_name(path.name + ".asc").write_bytes(
                f"signature of {relative}\n".encode()
            )
    # A checksum left by 'mvn deploy' that the bundle step regenerates.
    jar = root / ARTEFACTS[1]
    _ = jar.with_name(jar.name + ".md5").write_text("stale\n")


if __name__ == "__main__":
    build_m2repo(Path(sys.argv[1]))
