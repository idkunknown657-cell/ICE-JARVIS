"""End-to-end test of the GitHub-Releases updater against a local HTTP server
that mimics the GitHub API: release JSON + assets."""
import http.server
import hashlib
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

# Fake exe bytes with a known sha256
EXE_BYTES = b"MZ\x90\x00 fake JARVIS.exe payload " * 512
SHA = hashlib.sha256(EXE_BYTES).hexdigest()

PORT = 18799
RELEASES = {
    "tag_name": "v9.9.9",
    "body": "Test release with AGI brain",
    "assets": [
        {"name": "JARVIS.exe",
         "browser_download_url": f"http://127.0.0.1:{PORT}/JARVIS.exe"},
        {"name": "update.json",
         "browser_download_url": f"http://127.0.0.1:{PORT}/update.json"},
    ],
}
MANIFEST = {
    "version": "9.9.9",
    "channel": "stable",
    "sha256": SHA,
    "notes": "AGI brain test",
    "exe_url": "",
}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = None
        ctype = "application/json"
        if self.path == "/repos/test/repo/releases/latest":
            body = json.dumps(RELEASES).encode()
        elif self.path == "/JARVIS.exe":
            body = EXE_BYTES
            ctype = "application/octet-stream"
        elif self.path == "/update.json":
            body = json.dumps(MANIFEST).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

from core import updater as U
U.GITHUB_API = f"http://127.0.0.1:{PORT}/repos/{{repo}}/releases/latest"

# 0. no repo configured -> ValueError
try:
    U.check_for_update("")
    raise SystemExit("should have raised")
except ValueError:
    pass

# 1. repo with no release -> 404 raises (caller treats as no-update)
try:
    U.check_for_update("none/repo")
    raise SystemExit("404 should raise")
except Exception:
    pass

# 2. ahead -> UpdateInfo with sha256
info = U.check_for_update("test/repo")
assert info is not None, "update should be found"
assert info.version == "9.9.9", info
assert info.sha256 == SHA, info.sha256
print("check_for_update OK:", info.version, info.notes)

# 3. same version -> None (monkeypatch APP_VERSION via module constant)
U.APP_VERSION = "9.9.9"
assert U.check_for_update("test/repo") is None
U.APP_VERSION = "1.0.0"
print("no-update-when-equal OK")

# 4. download + sha verify
with tempfile.TemporaryDirectory() as td:
    # point _exe_dir at a temp dir so staging happens there
    import core.updater as U2
    U2._exe_dir = lambda: Path(td)
    staged = U2.download_update(info)
    assert staged.exists() and staged.stat().st_size == len(EXE_BYTES), staged
    assert U2._sha256_of(staged) == SHA
    # 5. apply_update in source mode -> False, no crash
    ok = U2.apply_update(staged, "9.9.9", "notes")
    assert ok is False, ok
    print("download+verify+source-apply OK:", staged.name, staged.stat().st_size, "bytes")

# 6. tampered sha -> refused
info_bad = U.UpdateInfo(**{**info.__dict__, "sha256": "0" * 64})
try:
    U2.download_update(info_bad)
    raise SystemExit("tampered download should have raised")
except ValueError as e:
    assert "sha256 mismatch" in str(e)
    print("tamper-refusal OK")

print("UPDATER TEST PASSED")