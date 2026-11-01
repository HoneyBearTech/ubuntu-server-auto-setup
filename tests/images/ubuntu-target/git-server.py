#!/usr/bin/env python3
"""A Git server for tests/kubernetes.sh: serves the repositories in /srv/git over HTTPS with git http-backend
(Git's smart HTTP protocol, which Argo CD needs), with the certificate and key in /srv/tls. Read-only: pushes are
refused. Test use only."""

import http.server
import os
import ssl
import subprocess
import sys

ROOT = "/srv/git"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._backend()

    def do_POST(self):
        self._backend()

    def _backend(self):
        path, _, query = self.path.partition("?")
        if "git-receive-pack" in self.path:
            self.send_error(403, "read-only")
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_PROJECT_ROOT": ROOT,
            "GIT_HTTP_EXPORT_ALL": "1",
            # The repository is the test's, mounted from the runner with another owner.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
            "REQUEST_METHOD": self.command,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
            "GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            "REMOTE_ADDR": self.client_address[0],
        }
        result = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, check=False)
        head, _, out = result.stdout.partition(b"\r\n\r\n")
        if not _:
            head, _, out = result.stdout.partition(b"\n\n")
        status, headers = 200, []
        for line in head.decode("latin-1").splitlines():
            name, _, value = line.partition(":")
            if name.lower() == "status":
                status = int(value.split()[0])
            elif name:
                headers.append((name, value.strip()))
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        if result.returncode:
            sys.stderr.write(result.stderr.decode("utf-8", "replace"))


context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/srv/tls/server.crt", "/srv/tls/server.key")
server = http.server.ThreadingHTTPServer(("0.0.0.0", 443), Handler)  # noqa: S104 (the test network only)
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
