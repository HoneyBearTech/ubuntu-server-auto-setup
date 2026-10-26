#!/usr/bin/env python3
"""Stand-ins for a mail relay and a webhook, for the notification steps of tests/updates.sh.

An SMTP server on 127.0.0.1 that offers STARTTLS with the given certificate and AUTH PLAIN or LOGIN, and an HTTP
server that accepts POSTs. Every message and every post is appended to a JSON-lines file:
{"kind": "mail", "tls": bool, "user": ..., "from": ..., "to": [...], "data": ...} or
{"kind": "webhook", "path": ..., "title": ..., "type": ..., "body": ...}.

Usage: notify_sink.py --cert C --key K --smtp-port P [--smtp-port P2 --cert2 C2 --key2 K2] --http-port H --out FILE
"""

import argparse
import base64
import http.server
import json
import socketserver
import ssl
import threading

LOCK = threading.Lock()
socketserver.TCPServer.allow_reuse_address = True


def record(out, entry):
    with LOCK, open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


class SMTPHandler(socketserver.BaseRequestHandler):
    def send(self, text):
        self.request.sendall((text + "\r\n").encode())

    def lines(self):
        buffer = b""
        while True:
            while b"\r\n" not in buffer:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            line, buffer = buffer.split(b"\r\n", 1)
            reset = yield line.decode("utf-8", "replace")
            if reset:  # after STARTTLS: nothing read before the handshake may be used
                buffer = b""

    def handle(self):
        mail = {"kind": "mail", "tls": False, "user": None, "from": None, "to": [], "data": ""}
        self.send("220 sink ESMTP")
        reader = self.lines()
        line = next(reader, None)
        while line is not None:
            verb, _, arg = line.partition(" ")
            verb = verb.upper()
            reset = False
            if verb in ("EHLO", "HELO"):
                extensions = ([] if mail["tls"] else ["STARTTLS"]) + ["AUTH PLAIN LOGIN"]
                self.send("250-sink")
                for i, extra in enumerate(extensions):
                    self.send(f"250{' ' if i == len(extensions) - 1 else '-'}{extra}")
            elif verb == "STARTTLS":
                self.send("220 go ahead")
                try:
                    self.request = self.server.context.wrap_socket(self.request, server_side=True)
                except ssl.SSLError:  # the client refused the certificate, as it should when it isn't trusted
                    return
                mail["tls"] = True
                reset = True
            elif verb == "AUTH":
                method, _, initial = arg.partition(" ")
                if method.upper() == "PLAIN":
                    parts = base64.b64decode(initial or "").split(b"\0")
                    mail["user"] = parts[1].decode() if len(parts) > 2 else None
                    mail["password_ok"] = len(parts) > 2 and parts[2] == self.server.password
                else:  # LOGIN
                    self.send("334 VXNlcm5hbWU6")
                    mail["user"] = base64.b64decode(next(reader)).decode()
                    self.send("334 UGFzc3dvcmQ6")
                    mail["password_ok"] = base64.b64decode(next(reader)) == self.server.password
                self.send("235 ok" if mail.get("password_ok") else "535 no")
            elif verb == "MAIL":
                mail["from"] = arg.split(":", 1)[-1].strip(" <>")
                self.send("250 ok")
            elif verb == "RCPT":
                mail["to"].append(arg.split(":", 1)[-1].strip(" <>"))
                self.send("250 ok")
            elif verb == "DATA":
                self.send("354 go ahead")
                data = []
                for body_line in reader:
                    if body_line == ".":
                        break
                    data.append(body_line[1:] if body_line.startswith("..") else body_line)
                mail["data"] = "\n".join(data)
                record(self.server.out, mail)
                self.send("250 queued")
            elif verb == "QUIT":
                self.send("221 bye")
                return
            else:
                self.send("250 ok")
            try:
                line = reader.send(True) if reset else next(reader)
            except StopIteration:
                return


class HookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server's name)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        record(self.server.out, {"kind": "webhook", "path": self.path, "title": self.headers.get("Title"),
                                 "type": self.headers.get("Content-Type"), "body": body})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def smtp_server(port, cert, key, out, password):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), SMTPHandler)
    server.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.context.load_cert_chain(cert, key)
    server.out, server.password = out, password.encode()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--smtp-port", type=int, required=True)
    parser.add_argument("--cert2")
    parser.add_argument("--key2")
    parser.add_argument("--smtp-port2", type=int)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--password", default="relay-secret")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    servers = [smtp_server(args.smtp_port, args.cert, args.key, args.out, args.password)]
    if args.smtp_port2:
        servers.append(smtp_server(args.smtp_port2, args.cert2, args.key2, args.out, args.password))
    hook = http.server.ThreadingHTTPServer(("127.0.0.1", args.http_port), HookHandler)
    hook.out = args.out
    servers.append(hook)
    for server in servers[:-1]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[-1].serve_forever()


if __name__ == "__main__":
    main()
