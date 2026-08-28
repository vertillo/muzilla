#!/usr/bin/env python3
# ponytail: minimal docker socket API filter (lite proxy for Q7/Q7b)
# Filters HostConfig.Privileged, PidMode/NetworkMode/IpcMode/UTSMode=host, and Binds to / or /etc etc.
# Allowlist volumes: /workspace, /tmp, /home/agent only. Bypass via SANDBOX_ALLOW_DOCKER_BYPASS is handled
# client-side (wrapper execs docker.real with DOCKER_HOST=unix:///host-docker.sock), so proxy always enforces.
import argparse
import contextlib
import http.server
import json
import os
import socket
import socketserver

BLOCK_PRIVILEGED = b'"Privileged":true'
BLOCK_HOST_MODE = [
    b'"PidMode":"host"',
    b'"NetworkMode":"host"',
    b'"IpcMode":"host"',
    b'"UTSMode":"host"',
]
BLOCK_SYSTEM_PRUNE = b"/system/prune"

ALLOWED_PREFIXES = [
    "/version",
    "/_ping",
    "/info",
    "/build",
    "/images/",
    "/images/json",
    "/containers/json",
    "/containers/create",
    "/containers/",
    "/networks/",
    "/volumes/",
]
BLOCKED_SUBSTRINGS = [
    "/exec",
    "/secrets",
    "/swarm",
    "/services",
    "/plugins",
    "/auth",
    "/system/prune",
    "/containers/prune",
    "/images/prune",
    "/volumes/prune",
    "/networks/prune",
]


def is_allowed(path: str) -> bool:
    # strip query string and optional API version prefix /v1.xx
    import re

    bare = path.split("?", 1)[0]
    m = re.match(r"^/v\d+\.\d+(/.+)$", bare)
    if m:
        bare = m.group(1)
    # check blocked substrings first (explicit deny wins)
    for substr in BLOCKED_SUBSTRINGS:
        if substr in bare:
            return False
    return any(bare == prefix.rstrip("/") or bare.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def is_blocked(path: str, body: bytes) -> tuple[bool, str]:
    # block by path first (covers version-prefixed paths too)
    bare = path.split("?", 1)[0]
    for substr in BLOCKED_SUBSTRINGS:
        if substr in bare:
            return True, f"blocked endpoint {substr}"
    # legacy system prune check (kept for ruff SIM102 compat)
    if (b"system/prune" in path.encode() or b"/prune" in path.encode()) and (
        b"/system/" in path.encode() or path == "/system/prune"
    ):
        return True, "system prune"
    if not body:
        return False, ""
    # semantic JSON validation — ponytail: no whitespace-sensitive byte matching
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            hc = data.get("HostConfig") if isinstance(data.get("HostConfig"), dict) else None
            # semantic checks for privileged/host modes (ponytail: must be outside binds collection)
            if hc is not None:
                if hc.get("Privileged") is True:
                    return True, "Privileged"
                for key in ("PidMode", "NetworkMode", "IpcMode", "UTSMode"):
                    val = hc.get(key)
                    if isinstance(val, str) and val.strip().lower() == "host":
                        return True, f"{key} host"
            # Binds + Mounts via HostConfig and top-level (ponytail: both, even if hc is None)
            binds: list[str] = []
            if hc is not None and isinstance(hc.get("Binds"), list):
                binds.extend([b for b in hc["Binds"] if isinstance(b, str)])
            if hc is not None:
                hc_mounts = hc.get("Mounts")
                if isinstance(hc_mounts, list):
                    for m in hc_mounts:
                        if isinstance(m, dict) and m.get("Source"):
                            src = str(m.get("Source", ""))
                            tgt = str(m.get("Target", ""))
                            binds.append(f"{src}:{tgt}" if tgt else src)
            # top-level Mounts (Docker API also accepts Mounts at top level for create) — always checked
            top_mounts = data.get("Mounts")
            if isinstance(top_mounts, list):
                for m in top_mounts:
                    if isinstance(m, dict) and m.get("Source"):
                        src = str(m.get("Source", ""))
                        tgt = str(m.get("Target", ""))
                        val = f"{src}:{tgt}" if tgt else src
                        if val not in binds:
                            binds.append(val)
            for b in binds:
                src = b.split(":")[0] if ":" in b else b
                src = src.strip()
                if not src:
                    continue
                if (
                    src == "/workspace"
                    or src.startswith("/workspace/")
                    or src == "/tmp"
                    or src.startswith("/tmp/")
                    or src == "/home/agent"
                    or src.startswith("/home/agent/")
                ):
                    continue
                if src.startswith("/"):
                    return True, f"host mount {src}"
        return False, ""
    except Exception:
        pass
    # non-JSON or parse failure — conservative byte scan for privileged/host (ponytail: fallback only)
    low = body.lower() if isinstance(body, bytes) else body.encode().lower()
    # ponytail: single if, no nested — was nested before, now collapsed
    if b'"privileged":true' in low or b'"privileged": true' in low or b'"privileged" : true' in low:
        return True, "Privileged"
    if (b'"/:/' in body or b'"/etc' in body or b'"/var' in body) and b"/workspace" not in body:
        return True, "host bind"
    return False, ""


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_proxy(self) -> None:
        # default deny allowlist (Q7/Q7b)
        if not is_allowed(self.path):
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            msg = json.dumps(
                {"message": f"sandbox-proxy: endpoint not allowed: {self.path}"}
            ).encode()
            self.wfile.write(msg)
            return
        # ponytail: for /build use raw tunnel (chunked tar), for others use Content-Length + JSON filter
        # version-prefixed build path must also use raw tunnel
        bare_no_q = self.path.split("?", 1)[0]
        if (
            bare_no_q.endswith("/build")
            or bare_no_q.startswith("/build")
            or "/images/" in self.path
            or "/build" in bare_no_q
        ):
            # raw tunnel without JSON inspection (allow build, image pull etc.)
            host_sock = self.server.host_sock  # type: ignore
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.connect(host_sock)
                # forward request line + headers + body as stream
                # Reconstruct request from raw headers (preserve Transfer-Encoding etc.)
                headers = ""
                for k, v in self.headers.items():
                    headers += f"{k}: {v}\r\n"
                # For build, body is tar (chunked or content-length) — stream via rfile -> sock
                req_head = f"{self.command} {self.path} HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n".encode()
                sock.sendall(req_head)
                # stream request body (if any) via select
                import select

                # forward request body
                # Content-Length or chunked: just pipe rfile -> sock until client done sending
                # Use non-blocking with timeout to avoid hanging
                sock.setblocking(False)
                self.connection.setblocking(False)
                # First, if Content-Length present, read that many bytes and forward
                clen = self.headers.get("Content-Length")
                if clen:
                    try:
                        cl = int(clen)
                        body = self.rfile.read(cl) if cl else b""
                        if body:
                            sock.sendall(body)
                    except Exception:
                        pass
                elif self.headers.get("Transfer-Encoding") == "chunked":
                    # pipe chunked body as is
                    while True:
                        r, _, _ = select.select([self.connection], [], [], 0.5)
                        if not r:
                            break
                        chunk = self.connection.recv(8192)
                        if not chunk:
                            break
                        sock.sendall(chunk)
                        if b"0\r\n\r\n" in chunk:
                            break
                # Now pipe response back to client
                sock.setblocking(False)
                while True:
                    r, _, _ = select.select([sock], [], [], 2.0)
                    if not r:
                        break
                    data = sock.recv(8192)
                    if not data:
                        break
                    try:
                        self.wfile.write(data)
                    except Exception:
                        break
            except Exception as e:
                with contextlib.suppress(Exception):
                    self.send_error(502, f"proxy error: {e}")
            finally:
                with contextlib.suppress(Exception):
                    sock.close()
            return
        content_length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(content_length) if content_length else b""
        # Check block
        blocked, reason = is_blocked(self.path, body)
        if blocked:
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            msg = json.dumps({"message": f"sandbox-proxy: refusing {reason}"}).encode()
            self.wfile.write(msg)
            return
        # forward to real docker socket (host.sock)
        host_sock = self.server.host_sock  # type: ignore
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(host_sock)
            headers = ""
            for k, v in self.headers.items():
                if k.lower() in (
                    "connection",
                    "keep-alive",
                    "proxy-connection",
                    "te",
                    "trailers",
                    "transfer-encoding",
                    "upgrade",
                ):
                    continue
                headers += f"{k}: {v}\r\n"
            req = f"{self.command} {self.path} HTTP/1.1\r\nHost: localhost\r\n{headers}Content-Length: {len(body)}\r\n\r\n".encode()
            sock.sendall(req + body)
            resp = sock.recv(65536)
            self.wfile.write(resp)
            sock.settimeout(0.5)
            while True:
                try:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                except TimeoutError:
                    break
        except Exception as e:
            with contextlib.suppress(Exception):
                self.send_error(502, f"proxy error: {e}")
        finally:
            with contextlib.suppress(Exception):
                sock.close()

    def do_GET(self) -> None:
        self.do_proxy()

    def do_POST(self) -> None:
        self.do_proxy()

    def do_DELETE(self) -> None:
        self.do_proxy()

    def do_PUT(self) -> None:
        self.do_proxy()

    def do_HEAD(self) -> None:
        self.do_proxy()

    def do_OPTIONS(self) -> None:
        self.do_proxy()

    def log_message(self, format: str, *args: object) -> None:
        # quiet unless error
        return


class ThreadingUnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-sock", default="/host.sock")
    ap.add_argument("--proxy-sock", default="/proxy/docker.sock")
    args = ap.parse_args()
    proxy_sock = args.proxy_sock
    host_sock = args.host_sock
    # ensure proxy dir exists
    os.makedirs(os.path.dirname(proxy_sock), exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(proxy_sock)
    server = ThreadingUnixStreamServer(proxy_sock, ProxyHandler)
    server.host_sock = host_sock  # type: ignore
    os.chmod(proxy_sock, 0o666)
    # also handle HTTP via Unix socket — BaseHTTPRequestHandler expects TCP, but UnixStreamServer works
    # Need to override socket type handling: http.server expects TCP, but we can still handle
    print(f"sandbox-proxy: {host_sock} -> {proxy_sock}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            os.unlink(proxy_sock)


if __name__ == "__main__":
    main()
