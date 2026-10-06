# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""A small in-memory stand-in for the Central Portal Publisher API.

It models the endpoints the action calls:

- ``POST /api/v1/publisher/upload`` stores a bundle and returns its ID
- ``POST /api/v1/publisher/status`` reports the deployment state
- ``POST /api/v1/publisher/deployment/<id>`` publishes a deployment
- ``GET /api/v1/publisher/deployment/<id>/download/<path>`` serves a file

Each deployment walks through a list of states: every status request
returns the head of the list and then advances, until one state remains.
``GET /_mock/deployments/<id>`` exposes the recorded publish calls.

Run it as a script to serve on a fixed port for an end-to-end job.
"""

from __future__ import annotations

import argparse
import base64
import email.parser
import email.policy
import io
import json
import re
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar, override
from urllib.parse import parse_qs, unquote, urlsplit

API = "/api/v1/publisher"
DEFAULT_STATES: dict[str, list[str]] = {
    "USER_MANAGED": ["PENDING", "VALIDATING", "VALIDATED"],
    "AUTOMATIC": ["PENDING", "VALIDATING", "VALIDATED", "PUBLISHING", "PUBLISHED"],
}
POM_PATH = re.compile(
    r"^(?P<group>.+)/(?P<artifact>[^/]+)/(?P<version>[^/]+)/[^/]+\.pom$"
)


def purls_for(files: dict[str, bytes]) -> list[str]:
    """Derive one Maven package URL per POM, as the Portal reports them."""
    purls: set[str] = set()
    for path in files:
        match = POM_PATH.match(path)
        if match:
            group = match["group"].replace("/", ".")
            purls.add(f"pkg:maven/{group}/{match['artifact']}@{match['version']}")
    return sorted(purls)


@dataclass
class Deployment:
    """One deployment and the scripted states it will report."""

    deployment_id: str
    files: dict[str, bytes]
    states: list[str]
    purls: list[str] | None = None
    errors: dict[str, list[str]] = field(default_factory=dict)
    publish_calls: int = 0

    @property
    def state(self) -> str:
        """Return the state the next status request reports."""
        return self.states[0]

    def report(self) -> dict[str, object]:
        """Return the status payload and advance to the next state."""
        payload: dict[str, object] = {
            "deploymentId": self.deployment_id,
            "deploymentName": "bundle.zip",
            "deploymentState": self.state,
            "purls": self.purls if self.purls is not None else purls_for(self.files),
        }
        if self.errors:
            payload["errors"] = self.errors
        if len(self.states) > 1:
            _ = self.states.pop(0)
        return payload


def read_bundle(data: bytes) -> dict[str, bytes]:
    """Return the regular files inside a bundle ZIP, keyed by path."""
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        return {
            name.removeprefix("./"): bundle.read(name)
            for name in bundle.namelist()
            if not name.endswith("/")
        }


class MockCentral:
    """In-memory Portal state shared by every request handler."""

    def __init__(self, username: str = "user", token: str = "token") -> None:
        """Create an empty Portal that accepts one credential pair."""
        secret = base64.b64encode(f"{username}:{token}".encode()).decode()
        self.authorization: str = f"Bearer {secret}"
        self.deployments: dict[str, Deployment] = {}
        self.upload_states: dict[str, list[str]] = {
            key: list(value) for key, value in DEFAULT_STATES.items()
        }
        self.after_publish: list[str] = ["PUBLISHING", "PUBLISHED"]
        self.download_states: set[str] = {"VALIDATED"}
        self.upload_reply: tuple[int, str] | None = None
        self.status_delay: float = 0.0
        self.lock: threading.Lock = threading.Lock()
        self.server: _Server | None = None

    def add(self, files: dict[str, bytes], states: list[str]) -> Deployment:
        """Register a deployment holding ``files`` and return it."""
        deployment = Deployment(str(uuid.uuid4()), dict(files), list(states))
        with self.lock:
            self.deployments[deployment.deployment_id] = deployment
        return deployment

    def start(self, port: int = 0) -> str:
        """Serve on loopback in a background thread and return the base URL."""
        self.server = _Server(("127.0.0.1", port), self)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        """Stop serving."""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


class _Server(ThreadingHTTPServer):
    daemon_threads: bool = True

    def __init__(self, address: tuple[str, int], central: MockCentral) -> None:
        super().__init__(address, _Handler)
        self.central: MockCentral = central

    @override
    def handle_error(self, request: object, client_address: object) -> None:
        # A client that gave up on a delayed reply is part of the test
        pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version: str = "HTTP/1.1"
    quiet: ClassVar[bool] = True

    @property
    def central(self) -> MockCentral:
        assert isinstance(self.server, _Server)
        return self.server.central

    @override
    def log_message(self, format: str, *args: object) -> None:
        if not self.quiet:
            super().log_message(format, *args)

    def reply(self, status: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        _ = self.wfile.write(body)

    def reply_json(self, status: int, payload: object) -> None:
        self.reply(status, json.dumps(payload).encode(), "application/json")

    def body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length)

    def authorised(self) -> bool:
        if self.headers.get("Authorization") == self.central.authorization:
            return True
        self.reply_json(401, {"error": "unauthorised"})
        return False

    def lookup(self, deployment_id: str) -> Deployment | None:
        deployment = self.central.deployments.get(deployment_id)
        if deployment is None:
            self.reply_json(404, {"error": f"no deployment {deployment_id}"})
        return deployment

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        parts = url.path.split("/")
        if url.path.startswith("/_mock/deployments/"):
            deployment = self.lookup(parts[3])
            if deployment:
                self.reply_json(
                    200,
                    {
                        "state": deployment.state,
                        "publishCalls": deployment.publish_calls,
                    },
                )
            return
        if not self.authorised():
            return
        prefix = f"{API}/deployment/"
        if url.path.startswith(prefix) and "/download/" in url.path:
            deployment_id, _, relative = url.path[len(prefix) :].partition("/download/")
            deployment = self.lookup(deployment_id)
            if deployment is None:
                return
            content = deployment.files.get(unquote(relative))
            if deployment.state not in self.central.download_states or content is None:
                self.reply_json(404, {"error": "not found"})
                return
            self.reply(200, content, "application/octet-stream")
            return
        self.reply_json(404, {"error": "unknown endpoint"})

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        payload = self.body()
        if url.path == f"{API}/status":
            time.sleep(self.central.status_delay)
        if not self.authorised():
            return
        with self.central.lock:
            if url.path == f"{API}/upload":
                self.upload(payload, query.get("publishingType", ["USER_MANAGED"])[0])
            elif url.path == f"{API}/status":
                deployment = self.lookup(query.get("id", [""])[0])
                if deployment:
                    self.reply_json(200, deployment.report())
            elif url.path.startswith(f"{API}/deployment/"):
                deployment = self.lookup(url.path.rsplit("/", 1)[1])
                if deployment is None:
                    return
                if deployment.state != "VALIDATED":
                    self.reply_json(400, {"error": f"deployment is {deployment.state}"})
                    return
                deployment.publish_calls += 1
                deployment.states = list(self.central.after_publish)
                self.reply(204)
            else:
                self.reply_json(404, {"error": "unknown endpoint"})

    def upload(self, payload: bytes, publishing_type: str) -> None:
        if self.central.upload_reply is not None:
            status, text = self.central.upload_reply
            self.reply(status, text.encode())
            return
        header = (
            f"Content-Type: {self.headers.get('Content-Type', '')}\r\n\r\n".encode()
        )
        message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            header + payload
        )
        bundle = b""
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") == "bundle":
                data = part.get_payload(decode=True)
                bundle = data if isinstance(data, bytes) else b""
        if not bundle or publishing_type not in self.central.upload_states:
            self.reply_json(400, {"error": "bad upload"})
            return
        deployment = Deployment(
            str(uuid.uuid4()),
            read_bundle(bundle),
            list(self.central.upload_states[publishing_type]),
        )
        self.central.deployments[deployment.deployment_id] = deployment
        self.reply(201, deployment.deployment_id.encode())


def main() -> None:
    """Serve the mock Portal until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--port", type=int, default=8765)
    _ = parser.add_argument("--username", default="user")
    _ = parser.add_argument("--token", default="token")
    args = parser.parse_args()
    port: int = args.port  # pyright: ignore[reportAny]
    central = MockCentral(str(args.username), str(args.token))  # pyright: ignore[reportAny]
    _Handler.quiet = False
    central.server = _Server(("127.0.0.1", port), central)
    print(f"Mock Central Portal on http://127.0.0.1:{port}", flush=True)
    central.server.serve_forever()


if __name__ == "__main__":
    main()
