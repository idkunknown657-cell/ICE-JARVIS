"""core/mail_auth.py — sign in to your mail provider instead of pasting a password.

WHY
    An app password works, but it means the user has to go and generate one, and
    a password is a long-lived secret that grants full account access. OAuth is
    what every mail client actually does: the user signs in on the provider's own
    page, the provider hands back a token that ICE can revoke from the account
    page at any time, and ICE never sees the password at all.

WHY THE SIGN-IN PAGE OPENS IN YOUR BROWSER AND NOT INSIDE ICE
    This is a deliberate choice, not a shortcut. Google blocks OAuth sign-in
    from embedded webviews outright — it detects the user agent and refuses with
    "this browser or app may not be secure" — and Microsoft discourages it for
    the same reason. An in-app popup would therefore look better and fail on the
    one provider most people use. Google's and Microsoft's own documentation for
    native apps says the same thing: open the system browser. What ICE does
    instead is show the sign-in state inside the app, so after you finish in the
    browser the account connects on its own and ICE says so.

THE FLOW (RFC 8252 — OAuth for native apps)
    1. ICE generates a PKCE verifier and a random `state`.
    2. It opens the provider's sign-in page, with a redirect back to a loopback
       HTTP server on this machine that exists for the length of the sign-in.
    3. The provider redirects the browser to 127.0.0.1 with a one-time code.
    4. ICE exchanges that code — proving it started the flow with the PKCE
       verifier — for an access token and a refresh token.
    5. The refresh token (the long-lived one) is stored in the user's local
       config, and the mail tool refreshes it silently from then on.

WHY YOU NEED A CLIENT ID
    A desktop app cannot keep a client secret — anything shipped in the binary is
    readable — so OAuth for native apps uses a PUBLIC client with PKCE instead.
    That still needs a client ID, and each provider issues those to a developer,
    not to a program. There is no way to ship one with ICE without impersonating
    someone else's application, so the user creates their own in about two
    minutes and pastes it in Settings. Every open-source mail client that cannot
    register a secret works this way. If that is more than you want to do, the
    app-password path next to it needs none of this and is just as fast.

NOTHING HERE RAISES and nothing here logs a token. `scrub()` runs over every
sentence this module produces, because a provider's error body is entirely
capable of quoting the credential that failed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

_SOCKET_TIMEOUT = 20.0
_SIGNIN_TIMEOUT = 300.0          # how long the loopback waits for the user
_REFRESH_LEEWAY = 120            # refresh this many seconds before expiry

# ── providers ────────────────────────────────────────────────────────────────
# `scope` is the minimum that grants IMAP and SMTP. Google's mail scope is the
# only one that permits both, which is why it looks broader than the others.
# Keys match MAIL_PRESETS in config_manager, so a provider has one name in the
# whole codebase and the hosts do not have to be translated between layers.
PROVIDERS = {
    "gmail": {
        "label": "Google / Gmail",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "https://mail.google.com/",
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
        "console": "https://console.cloud.google.com/apis/credentials",
        "console_hint": (
            "Create OAuth client ID → Application type: Desktop app → copy the "
            "Client ID. While the app is in Testing mode, add your own address "
            "under Audience → Test users, or Google will refuse the sign-in."),
    },
    "outlook": {
        "label": "Microsoft / Outlook",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": ("https://outlook.office.com/IMAP.AccessAsUser.All "
                  "https://outlook.office.com/SMTP.Send offline_access"),
        "imap_host": "outlook.office365.com",
        "smtp_host": "smtp.office365.com",
        "console": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps",
        "console_hint": (
            "New registration → Supported account types: personal Microsoft "
            "accounts → Add a platform → Mobile and desktop applications → tick "
            "http://localhost → copy the Application (client) ID."),
    },
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_verifier() -> str:
    """A PKCE code verifier: 43–128 characters from the unreserved set."""
    return _b64url(secrets.token_bytes(48))


def make_challenge(verifier: str) -> str:
    """S256: the challenge is the SHA-256 of the verifier, base64url, unpadded."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def make_state() -> str:
    return _b64url(secrets.token_bytes(24))


def authorize_url(provider: str, client_id: str, redirect_uri: str,
                  state: str, challenge: str, login_hint: str = "") -> str:
    """The provider's sign-in page, with everything it needs to send us back.

    `access_type=offline` + `prompt=consent` is what makes Google hand back a
    REFRESH token; without it the first sign-in returns an access token that dies
    in an hour and the user is asked to sign in again tomorrow. Microsoft spells
    the same intent `offline_access` in the scope.
    """
    p = PROVIDERS.get(provider) or {}
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": p.get("scope", ""),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider == "gmail":
        params.update({"access_type": "offline", "prompt": "consent",
                       "include_granted_scopes": "true"})
    if provider == "outlook":
        params["response_mode"] = "query"
    if login_hint:
        params["login_hint"] = login_hint
    return (p.get("auth_url", "") + "?" +
            urllib.parse.urlencode(params, quote_via=urllib.parse.quote))


def xoauth2_string(user: str, access_token: str) -> str:
    """The SASL string both IMAP and SMTP expect for XOAUTH2."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"


# ── secrets ──────────────────────────────────────────────────────────────────

def secrets_in_config() -> list[str]:
    """Every long-lived secret the mail feature holds, for scrubbing output.

    Only values long enough to be a real token: replacing a two-character string
    would mangle ordinary prose in an error message.
    """
    out: list[str] = []
    try:
        from memory.config_manager import load_api_keys
        data = load_api_keys()
    except Exception:
        return out
    mail = data.get("mail") if isinstance(data, dict) else None
    if not isinstance(mail, dict):
        return out
    for key in ("password",):
        v = str(mail.get(key) or "")
        if len(v) >= 4:
            out.append(v)
    oauth = mail.get("oauth")
    if isinstance(oauth, dict):
        for key in ("access_token", "refresh_token", "client_secret", "client_id"):
            v = str(oauth.get(key) or "")
            if len(v) >= 8:
                out.append(v)
    return out


def scrub(text: str) -> str:
    """Remove every stored mail secret from a sentence about to leave here."""
    out = str(text or "")
    for secret in secrets_in_config():
        out = out.replace(secret, "********")
    return out


def _explain_url_error(e: Exception) -> str:
    text = scrub(str(e))
    low = text.lower()
    if isinstance(e, urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = ""
        detail = _oauth_error_detail(body)
        return (f"the sign-in server answered HTTP {e.code}"
                + (f": {detail}" if detail else ""))
    if "timed out" in low or "timeout" in low:
        return "the sign-in server did not answer in time"
    if "certificate" in low or "ssl" in low:
        return f"the secure connection failed ({text})"
    if ("getaddrinfo" in low or "name or service not known" in low
            or "temporary failure in name resolution" in low):
        return "the sign-in server could not be found — check your connection"
    return text or "the sign-in server did not answer"


def _oauth_error_detail(body: str) -> str:
    """OAuth error bodies are JSON; the useful part is two short strings."""
    try:
        data = json.loads(body)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    code = str(data.get("error") or "").strip()
    desc = str(data.get("error_description") or "").strip()
    if code == "invalid_grant":
        return ("that sign-in code was already used or has expired — start the "
                "sign-in again")
    if code == "invalid_client":
        return "that client ID was rejected — check it was copied in full"
    if code == "redirect_uri_mismatch":
        return ("the provider rejected the loopback redirect. For a Desktop-app "
                "client ID this works as-is; a Web-application client ID needs "
                "http://127.0.0.1 added to its authorised redirect URIs")
    if code == "unauthorized_client":
        return "this client ID is not allowed to use the mail scope"
    if code == "access_denied":
        return "the sign-in was refused or cancelled"
    return " ".join(x for x in (code, desc) if x)[:300]


def _post_form(url: str, fields: dict) -> tuple[dict, str]:
    """POST a form and parse JSON. Returns (data, error sentence). Never raises."""
    body = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json",
                 "User-Agent": "ICE-JARVIS/1.0 (desktop mail sign-in)"})
    try:
        with urllib.request.urlopen(req, timeout=_SOCKET_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as e:
        return {}, _explain_url_error(e)
    try:
        data = json.loads(raw)
    except Exception:
        return {}, "the sign-in server did not return readable data"
    if isinstance(data, dict) and data.get("error"):
        return {}, _oauth_error_detail(raw) or str(data.get("error"))
    return (data if isinstance(data, dict) else {}), ""


# ── the loopback catcher ─────────────────────────────────────────────────────

_PAGE_OK = """<!doctype html><meta charset="utf-8">
<title>ICE — connected</title>
<style>
 body{margin:0;height:100vh;display:grid;place-items:center;background:#05070d;
      color:#dbe7f5;font:15px/1.6 "Segoe UI",system-ui,sans-serif}
 .b{text-align:center;padding:34px 44px;border:1px solid rgba(46,232,127,.32);
    border-radius:16px;background:rgba(46,232,127,.05)}
 h1{margin:0 0 8px;font-size:19px;color:#2ee87f}
 p{margin:0;color:#9fb2c9}
</style>
<div class="b"><h1>ICE is connected</h1>
<p>Your mail account is linked. You can close this tab.</p></div>
"""

_PAGE_BAD = """<!doctype html><meta charset="utf-8">
<title>ICE — sign-in failed</title>
<style>
 body{margin:0;height:100vh;display:grid;place-items:center;background:#05070d;
      color:#dbe7f5;font:15px/1.6 "Segoe UI",system-ui,sans-serif}
 .b{text-align:center;padding:34px 44px;border:1px solid rgba(255,92,122,.34);
    border-radius:16px;background:rgba(255,92,122,.06);max-width:520px}
 h1{margin:0 0 8px;font-size:19px;color:#ff5c7a}
 p{margin:0;color:#9fb2c9}
</style>
<div class="b"><h1>Sign-in did not finish</h1>
<p>__REASON__</p><p>You can close this tab and try again from ICE.</p></div>
"""


class _SignInServer:
    """A loopback HTTP server that exists only long enough to catch one redirect.

    Bound to 127.0.0.1 on an ephemeral port, so nothing outside this machine can
    reach it, and it is shut down the moment the code arrives — or when the
    sign-in times out, so a user who gives up does not leave a listener running.
    """

    def __init__(self, expected_state: str):
        self.expected_state = expected_state
        self.code = ""
        self.error = ""
        self._done = threading.Event()
        self._httpd = None
        self.port = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):        # no request line in the console
                pass

            def do_GET(self):
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                got_state = (params.get("state") or [""])[0]
                code = (params.get("code") or [""])[0]
                err = (params.get("error") or [""])[0]
                if err:
                    outer.error = str(err)
                    self._page(_PAGE_BAD, err)
                    outer._done.set()
                    return
                if not code:
                    # A stray request (favicon, a reload) must not end the flow.
                    self._page(_PAGE_BAD, "No sign-in code arrived on that request.")
                    return
                if got_state != outer.expected_state:
                    # CSRF guard: a code that does not match the state we sent
                    # belongs to somebody else's flow.
                    outer.error = "state mismatch"
                    self._page(_PAGE_BAD,
                               "That sign-in could not be verified as the one "
                               "ICE started.")
                    outer._done.set()
                    return
                outer.code = code
                self._page(_PAGE_OK, "")
                outer._done.set()

            def _page(self, template: str, reason: str):
                try:
                    body = template.replace("__REASON__",
                                            _html_escape(reason)).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except Exception:
                    pass

        self._handler_cls = Handler

    def __enter__(self):
        self._httpd = HTTPServer(("127.0.0.1", 0), self._handler_cls)
        self._httpd.timeout = 1.0
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._serve, daemon=True,
                         name="mail-signin-loopback").start()
        return self

    def _serve(self):
        deadline = time.monotonic() + _SIGNIN_TIMEOUT
        while not self._done.is_set() and time.monotonic() < deadline:
            try:
                self._httpd.handle_request()
            except Exception:
                break

    def wait(self, timeout: float = _SIGNIN_TIMEOUT) -> bool:
        return self._done.wait(timeout)

    def close(self):
        try:
            self._httpd.server_close()
        except Exception:
            pass

    def __exit__(self, *exc):
        self.close()


def _html_escape(text: str) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def free_loopback_port() -> int:
    """A port the OS says is free. Used only to build the URL early, so the UI
    can show the redirect destination; the real server binds port 0 itself."""
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
    except Exception:
        return 0


# ── the flow ─────────────────────────────────────────────────────────────────

class SignIn:
    """One sign-in attempt, in its own thread, with a state the UI can read.

    Kept as an object rather than a function so the interface can ask "how is it
    going" without blocking, and can cancel it. `phase` is one of:
      preparing · waiting · exchanging · connected · failed · cancelled
    """

    def __init__(self, provider: str, client_id: str, login_hint: str = ""):
        self.provider = provider
        self.client_id = str(client_id or "").strip()
        self.login_hint = login_hint
        self.phase = "preparing"
        self.message = ""
        self.url = ""
        self.account: dict = {}
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # -- state --
    def snapshot(self) -> dict:
        with self._lock:
            return {"phase": self.phase, "message": scrub(self.message),
                    "url": self.url, "provider": self.provider,
                    "configured": bool(self.account.get("address"))}

    def _set(self, phase: str, message: str = "") -> None:
        with self._lock:
            self.phase = phase
            if message:
                self.message = message

    def cancel(self) -> None:
        self._cancel.set()

    # -- run --
    def start(self, on_open=None) -> dict:
        """Begin the flow. Returns the first snapshot immediately — the UI needs
        the URL to show before the user has done anything."""
        err = self._validate()
        if err:
            self._set("failed", err)
            return self.snapshot()
        self._thread = threading.Thread(target=self._run, args=(on_open,),
                                        daemon=True, name="mail-signin")
        self._thread.start()
        # Give the worker a moment to bind and build the URL so the first poll
        # is not empty; the browser opens from the worker either way.
        for _ in range(50):
            if self.url or self.phase in ("failed", "waiting"):
                break
            time.sleep(0.02)
        return self.snapshot()

    def _validate(self) -> str:
        if self.provider not in PROVIDERS:
            return "Unknown mail provider for sign-in."
        if not self.client_id:
            return ("A client ID is needed for sign-in. It takes about two "
                    "minutes to create one — the button below opens the right "
                    "page — or use an app password instead, which needs none.")
        if "@" in self.client_id or " " in self.client_id:
            return ("That does not look like a client ID. Copy the value labelled "
                    "Client ID (or Application ID) — it is a long string ending "
                    "in .apps.googleusercontent.com for Google.")
        return ""

    def _run(self, on_open=None) -> None:
        p = PROVIDERS[self.provider]
        verifier = make_verifier()
        state = make_state()
        try:
            with _SignInServer(state) as server:
                redirect = f"http://127.0.0.1:{server.port}/"
                self.url = authorize_url(self.provider, self.client_id, redirect,
                                         state, make_challenge(verifier),
                                         self.login_hint)
                self._set("waiting", "Waiting for you to finish signing in. ICE "
                                     "will connect the moment you do.")
                if on_open:
                    try:
                        on_open(self.url)
                    except Exception:
                        pass
                while not server.wait(0.5):
                    if self._cancel.is_set():
                        self._set("cancelled", "Sign-in cancelled.")
                        return
                    if self.phase != "waiting":       # a later stage set a state
                        break
                if self._cancel.is_set():
                    self._set("cancelled", "Sign-in cancelled.")
                    return
                if not server.code:
                    if server.error == "state mismatch":
                        self._set("failed", "The sign-in could not be verified "
                                            "as the one ICE started. Nothing was "
                                            "connected.")
                    elif server.error:
                        self._set("failed",
                                  f"The provider refused the sign-in ({server.error}).")
                    else:
                        self._set("failed", "The sign-in timed out before it "
                                            "finished. Nothing was connected.")
                    return

                self._set("exchanging", "Signing in — swapping the code for a token.")
                data, err = _post_form(p["token_url"], {
                    "client_id": self.client_id,
                    "code": server.code,
                    "code_verifier": verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect,
                })
                if err:
                    self._set("failed", f"Could not finish the sign-in: {err}")
                    return
                refresh = str(data.get("refresh_token") or "")
                if not refresh:
                    self._set("failed",
                              "The provider returned no long-lived token, so ICE "
                              "would have to ask you to sign in every hour. "
                              "Try again and allow offline access.")
                    return
                self._store(data, refresh)
                self._set("connected", f"Connected {p['label']}.")
        except Exception as e:                        # never propagate
            self._set("failed", f"Sign-in failed: {scrub(str(e))}")

    def _store(self, data: dict, refresh: str) -> None:
        access = str(data.get("access_token") or "")
        try:
            expires_in = int(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        p = PROVIDERS[self.provider]
        self.account = {
            "provider": self.provider,
            "auth": "oauth",
            "address": self.login_hint or self._claimed_address(access),
            "imap_host": p["imap_host"],
            "imap_port": 993,
            "smtp_host": p["smtp_host"],
            "smtp_port": 587,
        }
        try:
            from memory.config_manager import save_mail_oauth
            save_mail_oauth(
                provider=self.provider,
                client_id=self.client_id,
                refresh_token=refresh,
                access_token=access,
                expires_at=time.time() + expires_in,
                address=self.account["address"],
                imap_host=p["imap_host"], smtp_host=p["smtp_host"],
            )
        except Exception as e:
            self.message = f"Signed in, but the token could not be saved: {e}"
            return
        self.account = self._read_account()

    def _claimed_address(self, access: str) -> str:
        return _whoami(self.provider, access)

    def _read_account(self) -> dict:
        try:
            from memory.config_manager import get_mail_config
            return get_mail_config()
        except Exception:
            return self.account


# ── who am I ─────────────────────────────────────────────────────────────────

def _whoami(provider: str, access: str) -> str:
    """Ask the provider which account a token belongs to.

    XOAUTH2 requires the address in the SASL string, so this is not cosmetic: a
    signed-in account with no address cannot log in at all. Gmail needs it as
    the full address; Outlook accepts the same value.
    """
    if not access:
        return ""
    url = ("https://www.googleapis.com/oauth2/v3/userinfo"
           if provider == "gmail"
           else "https://graph.microsoft.com/v1.0/me")
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {access}",
                          "User-Agent": "ICE-JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("email") or data.get("mail")
               or data.get("userPrincipalName") or "").strip()


def resolve_address() -> tuple[str, str]:
    """The address of the signed-in account. Returns (address, error sentence).

    A sign-in that could not name its account — the identity call timed out, the
    token was minted before the address was known — is repaired here on first
    use rather than reported as a broken account, because the token is valid and
    the mailbox is reachable; only the label is missing.
    """
    try:
        from memory.config_manager import get_mail_oauth, save_mail_address
        oauth = get_mail_oauth()
    except Exception:
        return "", "the mail sign-in details could not be read"
    if not oauth:
        return "", "no signed-in mail account"
    known = str(oauth.get("address") or "").strip()
    if known:
        return known, ""
    token, err = access_token()
    if err:
        return "", err
    found = _whoami(str(oauth.get("provider") or ""), token)
    if not found:
        return "", ("the mail provider would not say which account is signed "
                    "in — sign in again from Settings → Mail")
    try:
        save_mail_address(found)
    except Exception:
        pass
    return found, ""


# ── refreshing ───────────────────────────────────────────────────────────────

def access_token(force: bool = False) -> tuple[str, str]:
    """A usable access token, refreshed if it is close to expiring.

    Returns (token, error sentence). The leeway is deliberate: a token that
    expires mid-conversation would surface as an authentication failure in the
    middle of reading mail, which reads to the user as ICE being broken.
    """
    try:
        from memory.config_manager import (get_mail_oauth, save_mail_tokens)
        oauth = get_mail_oauth()
    except Exception:
        return "", "the mail sign-in details could not be read"
    if not oauth:
        return "", "no signed-in mail account"
    token = str(oauth.get("access_token") or "")
    try:
        expires_at = float(oauth.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    if token and not force and expires_at - time.time() > _REFRESH_LEEWAY:
        return token, ""
    refresh = str(oauth.get("refresh_token") or "")
    client_id = str(oauth.get("client_id") or "")
    provider = str(oauth.get("provider") or "")
    if not (refresh and client_id):
        return "", ("the saved sign-in is incomplete — sign in again from "
                    "Settings → Mail")
    p = PROVIDERS.get(provider) or {}
    if not p:
        return "", "the saved sign-in names a provider ICE does not know"
    data, err = _post_form(p["token_url"], {
        "client_id": client_id,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
        "scope": p["scope"],
    })
    if err:
        return "", f"could not refresh the mail sign-in: {err}"
    new = str(data.get("access_token") or "")
    if not new:
        return "", "the mail provider returned no access token"
    try:
        expires_in = int(data.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    try:
        save_mail_tokens(access_token=new, expires_at=time.time() + expires_in,
                         refresh_token=str(data.get("refresh_token") or ""))
    except Exception:
        pass
    return new, ""


__all__ = [
    "PROVIDERS", "SignIn", "access_token", "authorize_url", "free_loopback_port",
    "make_challenge", "make_state", "make_verifier", "resolve_address", "scrub",
    "secrets_in_config", "xoauth2_string",
]
