"""
actions/email_agent.py — actually reading and sending mail, not driving a browser.

WHY
    "Check my mail" used to mean opening Gmail in a browser and clicking around
    the interface. That works, but it is the wrong shape for the request: the
    user wants to know WHAT is in the inbox, and a screen-driving loop answers
    that by describing a web page. Mail over IMAP is a read of the real mailbox,
    in one step, with no window to open and nothing to click.

WHAT IT DOES
    status    — is an account configured, and does it actually connect
    inbox     — the most recent messages (optionally unread only)
    search    — from / subject / body / since / unseen
    read      — the full text of one message
    mark_read — flag a message as read
    send      — write a new mail, or reply to one

DESIGN RULES, AND WHY EACH ONE IS HERE
    * This is the only tool that touches credentials, so the password never
      enters a log line, an error message or a return value. `_scrub()` runs over
      every sentence this module produces, because the surest way to leak a
      secret is to quote a library's own exception text.
    * Reading is done with BODY.PEEK, so asking JARVIS what arrived does not
      silently mark everything as read. Mailboxes are state the user owns;
      an assistant that quietly changes them is a bug, not a feature.
    * Sending is irreversible, so it goes behind core.confirm — the same
      on-screen gate every other irreversible action uses. Nothing is sent
      until the user presses CONFIRM, and the gate shows the recipient and
      subject before they do.
    * Every public function returns a sentence and never raises. A tool result
      is something the assistant has to be able to say out loud.

STDLIB ONLY. imaplib, smtplib, email — no new dependency for a capability this
central, and nothing to install for a user who just wants their inbox.
"""
from __future__ import annotations

import email
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, parsedate_to_datetime, parseaddr

try:
    from memory.config_manager import get_mail_config
except Exception:                                       # pragma: no cover
    def get_mail_config() -> dict:                      # type: ignore
        return {}

try:
    from core.mail_auth import access_token as _access_token
    from core.mail_auth import scrub as _scrub_secrets
    from core.mail_auth import secrets_in_config as _mail_secrets
    from core.mail_auth import xoauth2_string as _xoauth2_string
except Exception:                                      # pragma: no cover
    def _access_token(force: bool = False):             # type: ignore
        return "", "sign-in support is unavailable"

    def _scrub_secrets(text: str) -> str:               # type: ignore
        return str(text or "")

    def _mail_secrets() -> list:                        # type: ignore
        return []

    def _xoauth2_string(user: str, access: str) -> str:  # type: ignore
        return f"user={user}\x01auth=Bearer {access}\x01\x01"

# Bounds. A voice assistant reads these aloud, so the cap is about what a person
# can listen to, not about what the mailbox holds.
_MAX_ROWS = 15
_DEFAULT_ROWS = 5
_BODY_CHARS = 1400
_TIMEOUT = 20.0

# Folders to try, in order. Different providers spell them differently and the
# user should not have to know which one their account uses.
_INBOX_CANDIDATES = ("INBOX",)
_SENT_CANDIDATES = ("Sent", "[Gmail]/Sent Mail", "Sent Items", "Sent Messages")


def _log(message: str, player=None) -> None:
    print(f"[Mail] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


def _scrub(text: str) -> str:
    """Remove every stored mail secret from anything leaving this module.

    Every failure path funnels through here. An IMAP or SMTP library is free to
    include the command it failed on in its exception text, and quoting that
    verbatim into a log line or a spoken sentence is exactly how a credential
    ends up in a file the user later shares. The token now arrives from
    mail_auth, which also removes it from its own error strings, so a secret that
    passes through either module is masked in both.
    """
    out = _scrub_secrets(str(text or ""))
    try:
        pw = str((get_mail_config() or {}).get("password") or "")
    except Exception:
        pw = ""
    if len(pw) >= 4:
        out = out.replace(pw, "********")
    return out


def _sentence(text: str) -> str:
    return _scrub(text).strip()


# ── account ──────────────────────────────────────────────────────────────────

def _account() -> tuple[dict, str]:
    """The configured account, or a sentence explaining what is missing.

    A signed-in account has no password at all, so the requirement is on the
    credential that account actually uses rather than on the field named
    "password". Checking the wrong one would tell a user who just signed in with
    Google that their account is missing a password.
    """
    try:
        cfg = get_mail_config() or {}
    except Exception:
        cfg = {}
    signed_in = cfg.get("auth") == "oauth"
    if signed_in and not cfg.get("address"):
        # XOAUTH2 needs the address in the SASL string, so a sign-in that never
        # learned its own address has to be repaired before it can log in.
        try:
            from core.mail_auth import resolve_address
            found, _why = resolve_address()
        except Exception:
            found = ""
        if found:
            cfg["address"] = found
    if not (cfg.get("address") or signed_in):
        return {}, ("No mail account is set up yet. Open Settings → Mail — you can "
                    "sign in with Google or Microsoft there, or enter an address "
                    "and an app password. For Gmail an app password is what "
                    "works, not your normal Google password.")
    if not signed_in and not cfg.get("password"):
        return {}, ("The mail account is missing its password. Add it in "
                    "Settings → Mail.")
    if not cfg.get("imap_host"):
        return {}, ("The mail account has no IMAP server. Pick your provider in "
                    "Settings → Mail, or enter the server name.")
    return cfg, ""


def _imap(cfg: dict):
    """Connect and log in. Returns (connection, error sentence). Never raises."""
    try:
        conn = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg["imap_port"]),
                                 timeout=_TIMEOUT)
    except Exception as e:
        return None, _sentence(
            f"I could not reach {cfg['imap_host']} on port {cfg['imap_port']}: "
            f"{_explain(e)}")
    try:
        if cfg.get("auth") == "oauth":
            token, err = _access_token()
            if err:
                _close(conn)
                return None, _sentence(
                    f"The mail sign-in needs renewing ({err}). Open Settings → "
                    f"Mail and sign in again.")
            # XOAUTH2 sends the token, never the password: for a signed-in
            # account there is no password to send.
            conn.authenticate("XOAUTH2",
                              lambda _challenge: _xoauth2_string(
                                  cfg.get("address") or "", token).encode("ascii"))
        else:
            conn.login(cfg["address"], cfg["password"])
    except imaplib.IMAP4.error as e:
        try:
            conn.logout()
        except Exception:
            pass
        text = _scrub(str(e)).lower()
        if cfg.get("auth") == "oauth":
            return None, _sentence(
                f"{cfg.get('address') or 'The account'} was refused by the mail "
                f"server. The sign-in may have been revoked from the account "
                f"page — open Settings → Mail and sign in again.")
        if "application-specific password" in text or "invalid credentials" in text \
           or "authenticationfailed" in text or "login failed" in text:
            return None, _sentence(
                f"{cfg['address']} was refused by the server. Most providers "
                f"reject a normal account password from a mail client — generate "
                f"an app password and use that in Settings → Mail.")
        return None, _sentence(f"The mail server refused the login: {e}")
    except Exception as e:
        try:
            conn.logout()
        except Exception:
            pass
        return None, _sentence(f"The mail server refused the login: {_explain(e)}")
    return conn, ""


def _explain(e: Exception) -> str:
    """One plain clause for the common socket and TLS failures."""
    text = _scrub(str(e))
    low = text.lower()
    if "timed out" in low or "timeout" in low:
        return "the connection timed out"
    if "getaddrinfo" in low or "name or service not known" in low or \
       "nodename nor servname" in low or "temporary failure in name resolution" in low:
        return "that server name could not be found — check the spelling"
    if "certificate" in low or "ssl" in low:
        return f"the secure connection failed ({text})"
    if "connection refused" in low:
        return "the server refused the connection on that port"
    if "network is unreachable" in low or "no route" in low:
        return "there is no network route to it"
    return text or "the server did not answer"


def _select(conn, mailbox: str) -> bool:
    """Select a mailbox read-only. Read-only is deliberate: opening a mailbox for
    reading must not change flags on the server."""
    try:
        typ, _ = conn.select(mailbox, readonly=True)
        return typ == "OK"
    except Exception:
        return False


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass
    try:
        conn.logout()
    except Exception:
        pass


# ── message reading ──────────────────────────────────────────────────────────

def _hdr(value) -> str:
    """Decode a possibly RFC-2047-encoded header into real text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except Exception:
        return str(value).strip()


def _when(value) -> datetime | None:
    try:
        dt = parsedate_to_datetime(str(value))
    except Exception:
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "unknown date"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    if secs < 172800:
        return f"{int(secs // 3600)} h ago"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)} days ago"
    return dt.strftime("%d %b")


def _body_text(msg: email.message.Message, limit: int = _BODY_CHARS) -> str:
    """The readable body: prefer plain text, fall back to de-tagged HTML.

    No HTML is spoken aloud. A tag-stripped document is not perfect prose, but
    reading markup or raw entities as speech is worse than reading slightly
    compressed prose.
    """
    plain, html = "", ""
    try:
        parts = msg.walk() if msg.is_multipart() else [msg]
    except Exception:
        parts = [msg]
    for part in parts:
        try:
            if part.get_content_maintype() == "multipart":
                continue
            ctype = (part.get_content_type() or "").lower()
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except Exception:
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain":
                plain += text
            else:
                html += text
        except Exception:
            continue

    text = plain or _strip_html(html)
    if not text and msg.get_payload() and isinstance(msg.get_payload(), str):
        text = _strip_html(msg.get_payload())
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return ""
    if len(text) > limit:
        # Say where it was cut, so a truncated answer is never mistaken for the
        # whole message — an assistant that stops mid-sentence silently is
        # indistinguishable from one that lost the rest.
        return text[:limit].rsplit(" ", 1)[0] + " … (cut off; ask me to continue)"
    return text


_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BRK = re.compile(r"<\s*(br|/p|/div|/tr|/h[1-6])\s*/?\s*>", re.I)
_ANY = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = _TAG.sub(" ", html)
    text = _BRK.sub("\n", text)
    text = _ANY.sub("", text)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                    ("&apos;", "'"), ("&mdash;", "—"), ("&ndash;", "–")):
        text = text.replace(ent, ch)
    return text


def _row(uid: str, msg: email.message.Message, seen: bool) -> dict:
    who = _hdr(msg.get("From"))
    name, addr = parseaddr(who)
    return {
        "uid": uid,
        "from": name or addr or who or "unknown sender",
        "address": addr,
        "subject": _hdr(msg.get("Subject")) or "(no subject)",
        "when": _when(msg.get("Date")),
        "unread": not seen,
    }


def _fetch_headers(conn, uids: list[str]) -> list[dict]:
    """Fetch only the headers for the given uids — never the bodies, so listing
    an inbox of 15 messages does not download 15 messages."""
    out: list[dict] = []
    for uid in uids:
        try:
            typ, data = conn.uid(
                "FETCH", uid, "(BODY.PEEK[HEADER] FLAGS)")
        except Exception:
            continue
        if typ != "OK" or not data:
            continue
        raw, flags = None, ""
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                raw = item[1]
                if len(item) > 2:
                    flags = str(item[2] or "")
            elif isinstance(item, (bytes, bytearray)):
                m = re.search(rb"FLAGS \(([^)]*)\)", bytes(item))
                if m:
                    flags = m.group(1).decode("ascii", "replace")
        if not raw:
            continue
        try:
            msg = email.message_from_bytes(raw)
        except Exception:
            continue
        out.append(_row(uid, msg, "\\Seen" in flags))
    return out


def _search_uids(conn, criteria: str, limit: int) -> tuple[list[str], str]:
    """Newest-first uids matching an IMAP search string."""
    for charset in ("UTF-8", None):
        try:
            if charset:
                typ, data = conn.uid("SEARCH", "CHARSET", charset, criteria)
            else:
                typ, data = conn.uid("SEARCH", criteria)
        except Exception as e:
            return [], _sentence(f"the search failed: {_explain(e)}")
        if typ == "OK":
            break
    else:
        return [], "the mail server rejected the search."
    raw = b" ".join(d for d in (data or []) if isinstance(d, (bytes, bytearray)))
    uids = raw.split()
    # Newest first: IMAP returns ascending uids, and "what just arrived" is the
    # question being asked almost every time.
    return [u.decode("ascii", "replace") for u in reversed(uids)][:limit], ""


def _quote(value: str) -> str:
    """IMAP search arguments must be quoted, and any quote inside them escaped,
    or a subject containing a space silently becomes two search terms."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _list_sentence(rows: list[dict], label: str, unread_only: bool) -> str:
    if not rows:
        return (f"There are no unread messages in {label}." if unread_only
                else f"{label} has no messages matching that.")
    unread = sum(1 for r in rows if r["unread"])
    head = f"{len(rows)} message{'s' if len(rows) != 1 else ''}"
    if unread:
        head += f", {unread} unread"
    lines = [f"{head} in {label}:"]
    for i, r in enumerate(rows, 1):
        flag = "unread" if r["unread"] else "read"
        lines.append(f"{i}. {r['from']} — {r['subject']} ({_ago(r['when'])}, {flag})")
    lines.append("Say 'read number 2' for the full message.")
    return "\n".join(lines)


# ── actions ──────────────────────────────────────────────────────────────────

def _status(params: dict = None, player=None) -> str:
    cfg, err = _account()
    if err:
        return err
    conn, err = _imap(cfg)
    if err:
        return err
    try:
        rows: list[dict] = []
        if _select(conn, "INBOX"):
            uids, _ = _search_uids(conn, "UNSEEN", _DEFAULT_ROWS)
            rows = _fetch_headers(conn, uids)
        unread = len(rows)
        # "signed in" is worth saying out loud: it is the difference between an
        # account that will keep working unattended and one that will stop when
        # the app password is rotated.
        how = "signed in" if cfg.get("auth") == "oauth" else "app password"
        return (f"Mail is connected as {cfg.get('address') or 'the signed-in account'}"
                f" ({how}). {unread} unread in the inbox, newest: "
                f"{(rows[0]['from'] + ' — ' + rows[0]['subject']) if rows else 'nothing waiting'}.")
    finally:
        _close(conn)


def _inbox(params: dict, player=None) -> str:
    cfg, err = _account()
    if err:
        return err
    unread_only = bool(params.get("unread")) or bool(params.get("unseen"))
    limit = _limit(params)
    conn, err = _imap(cfg)
    if err:
        return err
    try:
        if not _select(conn, "INBOX"):
            return "I could not open the inbox folder on that account."
        uids, err = _search_uids(conn, "UNSEEN" if unread_only else "ALL", limit)
        if err:
            return err
        rows = _fetch_headers(conn, uids)
        return _list_sentence(rows, "the inbox", unread_only)
    finally:
        _close(conn)


def _search(params: dict, player=None) -> str:
    cfg, err = _account()
    if err:
        return err

    sender = str(params.get("from") or params.get("sender") or "").strip()
    subject = str(params.get("subject") or "").strip()
    body = str(params.get("contains") or params.get("body") or "").strip()
    since = str(params.get("since") or "").strip()
    unread_only = bool(params.get("unread")) or bool(params.get("unseen"))
    limit = _limit(params)

    terms: list[str] = []
    if sender:
        terms.append(f"FROM {_quote(sender)}")
    if subject:
        terms.append(f"SUBJECT {_quote(subject)}")
    if body:
        terms.append(f"TEXT {_quote(body)}")
    if unread_only:
        terms.append("UNSEEN")
    if since:
        stamp = _imap_date(since)
        if stamp is None:
            return (f"I could not read '{since}' as a date. Try 'yesterday', "
                    f"'3 days ago', or '2026-09-01'.")
        terms.append(f"SINCE {stamp}")
    if not terms:
        return ("Tell me what to search for: a sender, a subject, a word in the "
                "message, a date, or unread only.")

    conn, err = _imap(cfg)
    if err:
        return err
    try:
        if not _select(conn, "INBOX"):
            return "I could not open the inbox folder on that account."
        uids, err = _search_uids(conn, " ".join(terms), limit)
        if err:
            return err
        rows = _fetch_headers(conn, uids)
        described = ", ".join(t.split(" ", 1)[0].lower() for t in terms)
        return _list_sentence(rows, f"the inbox (matching {described})", unread_only)
    finally:
        _close(conn)


def _imap_date(text: str) -> str | None:
    """A date the way people say it → IMAP's DD-Mon-YYYY. Returns None when the
    input cannot be understood, rather than silently searching a wrong date."""
    low = text.strip().lower()
    today = datetime.now()
    if low in ("today",):
        return today.strftime("%d-%b-%Y")
    if low in ("yesterday",):
        return (today - timedelta(days=1)).strftime("%d-%b-%Y")
    m = re.match(r"^(\d{1,3})\s*(day|days|week|weeks|month|months)\s*ago$", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * (7 if unit.startswith("week") else
                    30 if unit.startswith("month") else 1)
        return (today - timedelta(days=days)).strftime("%d-%b-%Y")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    return None


def _fetch_one(conn, uid: str):
    """(Message, seen_flag) for one uid, fetched with PEEK so reading a message
    does not mark it read behind the user's back."""
    try:
        typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[] FLAGS)")
    except Exception as e:
        return None, False, _sentence(f"I could not fetch that message: {_explain(e)}")
    if typ != "OK" or not data:
        return None, False, "That message is no longer in the mailbox."
    raw, flags = None, ""
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            raw = item[1]
            if len(item) > 2:
                flags = str(item[2] or "")
        elif isinstance(item, (bytes, bytearray)):
            m = re.search(rb"FLAGS \(([^)]*)\)", bytes(item))
            if m:
                flags = m.group(1).decode("ascii", "replace")
    if not raw:
        return None, False, "That message could not be decoded."
    try:
        return email.message_from_bytes(raw), "\\Seen" in flags, ""
    except Exception as e:
        return None, False, _sentence(f"That message could not be decoded: {e}")


def _resolve_uid(conn, params: dict) -> tuple[str, str]:
    """Work out which message the user means.

    Three ways to say the same thing, because all three are natural: a uid the
    previous listing printed, a 1-based position in the most recent listing, or
    "the last one" / "that one". Resolving without a uid re-runs the unread
    search, which is what "read the first one" almost always refers to.
    """
    uid = str(params.get("uid") or params.get("id") or "").strip()
    if uid.isdigit():
        return uid, ""
    n = params.get("number") or params.get("index")
    try:
        n = int(n) if n not in (None, "") else None
    except (TypeError, ValueError):
        n = None
    if n is None:
        n = 1
    if n < 1:
        n = 1
    limit = max(n, _DEFAULT_ROWS)
    uids, err = _search_uids(conn, "ALL", limit)
    if err:
        return "", err
    if not uids:
        return "", "The inbox is empty."
    if n > len(uids):
        return "", f"There are only {len(uids)} recent messages to choose from."
    return uids[n - 1], ""


def _read(params: dict, player=None) -> str:
    cfg, err = _account()
    if err:
        return err
    conn, err = _imap(cfg)
    if err:
        return err
    try:
        if not _select(conn, "INBOX"):
            return "I could not open the inbox folder on that account."
        uid, err = _resolve_uid(conn, params)
        if err:
            return err
        msg, seen, err = _fetch_one(conn, uid)
        if err:
            return err
        who = _hdr(msg.get("From"))
        name, addr = parseaddr(who)
        subject = _hdr(msg.get("Subject")) or "(no subject)"
        when = _when(msg.get("Date"))
        body = _body_text(msg)
        lines = [f"{name or addr or 'Unknown sender'} — {subject} "
                 f"({_ago(when)}{', unread' if not seen else ''})"]
        lines.append(body if body else "(this message has no readable text body)")
        lines.append(f"uid {uid} — say 'mark that as read' or 'reply to it'.")
        return "\n".join(lines)
    finally:
        _close(conn)


def _mark_read(params: dict, player=None) -> str:
    cfg, err = _account()
    if err:
        return err
    conn, err = _imap(cfg)
    if err:
        return err
    try:
        # Read-write this time: the whole point of the action is to change a flag.
        try:
            typ, _ = conn.select("INBOX")
        except Exception:
            typ = "NO"
        if typ != "OK":
            return "I could not open the inbox folder on that account."
        uid, err = _resolve_uid(conn, params)
        if err:
            return err
        try:
            conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        except Exception as e:
            return _sentence(f"I could not mark it as read: {_explain(e)}")
        return "Marked as read."
    finally:
        _close(conn)


# ── sending ──────────────────────────────────────────────────────────────────

def _send(params: dict, player=None) -> str:
    cfg, err = _account()
    if err:
        return err
    to = str(params.get("to") or params.get("recipient") or "").strip()
    subject = str(params.get("subject") or "").strip()
    body = str(params.get("body") or params.get("message") or "").strip()
    reply_uid = str(params.get("reply_to") or params.get("reply_uid") or "").strip()

    quoted: dict = {}
    if reply_uid:
        conn, err = _imap(cfg)
        if err:
            return err
        try:
            if not _select(conn, "INBOX"):
                return "I could not open the inbox folder on that account."
            msg, _, err = _fetch_one(conn, reply_uid)
            if err:
                return err
            _, addr = parseaddr(_hdr(msg.get("From")))
            if not to:
                to = addr
            if not subject:
                orig = _hdr(msg.get("Subject")) or ""
                subject = orig if orig.lower().startswith("re:") else f"Re: {orig}"
            quoted = {"in_reply_to": _hdr(msg.get("Message-ID")),
                      "references": _hdr(msg.get("Message-ID")),
                      "orig_from": _hdr(msg.get("From")),
                      "orig_date": _hdr(msg.get("Date"))}
        finally:
            _close(conn)

    if not to:
        return "Who should I send it to?"
    if "@" not in to:
        return (f"'{to}' does not look like an email address. Give me the full "
                f"address to send to.")
    if not body:
        return "What should the message say?"

    title = f"Send this email to {to}"
    detail = (f"Subject: {subject or '(no subject)'}\n\n"
              f"{body[:400]}{'…' if len(body) > 400 else ''}")

    import core.confirm as confirm

    def _deliver() -> str:
        return _deliver_now(cfg, to, subject, body, quoted)

    return confirm.request(f"mail:{to}:{subject[:40]}", title, detail, _deliver)


def _deliver_now(cfg: dict, to: str, subject: str, body: str,
                 quoted: dict | None = None) -> str:
    """The actual send. Only ever reached through the confirmation gate."""
    quoted = quoted or {}
    msg = EmailMessage()
    msg["From"] = cfg["address"]
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg["Date"] = formatdate(localtime=True)
    if quoted.get("in_reply_to"):
        msg["In-Reply-To"] = quoted["in_reply_to"]
    if quoted.get("references"):
        msg["References"] = quoted["references"]
    if quoted.get("orig_from"):
        attribution = f"\n\nOn {quoted.get('orig_date') or 'an earlier date'}, " \
                      f"{quoted['orig_from']} wrote:"
        msg.set_content(body + attribution + "\n> (original message quoted in " \
                       "your mail client)")
    else:
        msg.set_content(body)

    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=_TIMEOUT)
            try:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            except smtplib.SMTPException:
                pass          # provider on 587 without STARTTLS
    except Exception as e:
        return _sentence(f"I could not reach {host} to send it: {_explain(e)}")
    try:
        if cfg.get("auth") == "oauth":
            token, err = _access_token()
            if err:
                return _sentence(
                    f"Nothing was sent — the mail sign-in needs renewing ({err}). "
                    f"Sign in again from Settings → Mail.")
            # smtplib builds `AUTH XOAUTH2 <base64>` from the initial response
            # this returns, which is the mechanism both providers expect.
            server.auth("XOAUTH2",
                        lambda: _xoauth2_string(cfg.get("address") or "",
                                                token))
        else:
            server.login(cfg["address"], cfg["password"])
        server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        if cfg.get("auth") == "oauth":
            return _sentence(
                f"The server refused the sign-in, so nothing was sent. Open "
                f"Settings → Mail and sign in again.")
        return _sentence(
            f"The server refused the login, so nothing was sent. Most providers "
            f"need an app password rather than the account password.")
    except Exception as e:
        return _sentence(f"The message was not sent: {_explain(e)}")
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return f"Sent to {to}."


# ── entry point ──────────────────────────────────────────────────────────────

def _limit(params: dict) -> int:
    """How many rows to list. Documented as 1.._MAX_ROWS.

    An explicitly requested 0 clamps to 1 rather than quietly becoming the
    default: `or` here would treat a real 0 as absent and hand back five
    messages the caller did not ask for, which is the kind of surprise that
    makes a cap untrustworthy.
    """
    raw = params.get("limit")
    if raw in (None, ""):
        raw = params.get("count")
    if raw in (None, ""):
        return _DEFAULT_ROWS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_ROWS
    return max(1, min(n, _MAX_ROWS))


_HANDLERS = {
    "status": _status,
    "inbox": _inbox,
    "search": _search,
    "read": _read,
    "mark_read": _mark_read,
    "send": _send,
}

_SYNONYMS = {
    "check": "inbox", "new": "inbox", "unread": "inbox", "list": "inbox",
    "recent": "inbox", "messages": "inbox", "mail": "inbox", "emails": "inbox",
    "open": "read", "show": "read", "body": "read",
    "find": "search", "look": "search", "query": "search",
    "mark": "mark_read", "seen": "mark_read", "read_flag": "mark_read",
    "reply": "send", "write": "send", "compose": "send", "email": "send",
    "connect": "status", "account": "status", "configured": "status",
}


def email_agent(parameters: dict = None, player=None, session_memory=None) -> str:
    """Read and send mail over IMAP/SMTP. Never raises."""
    params = dict(parameters or {})

    def _val(names):
        for n in names:
            if params.get(n) not in (None, ""):
                return params.get(n)
        return ""

    raw_action = str(_val(("action", "what", "command", "operation"))).strip().lower()
    action = raw_action.replace(" ", "_")
    if action not in _HANDLERS:
        action = _SYNONYMS.get(action, "")
    if not action:
        # No action named: infer the intent from whatever else was sent, so
        # "any new emails?" with no action still lists the inbox.
        if _val(("body", "message", "text")) and _val(("to", "recipient")):
            action = "send"
        elif _val(("from", "sender", "subject", "contains", "since", "unseen")):
            action = "search"
        elif _val(("uid", "id", "number", "index")):
            action = "read"
        else:
            action = "inbox"
    if action == "send" and _val(("reply_to", "reply_uid")) and not _val(("body", "message")):
        return ("What should I reply with? Give me the text and I will put it on "
                "screen for you to confirm.")

    fn = _HANDLERS[action]
    try:
        return _sentence(fn(params, player))
    except Exception as e:                              # never propagate
        _log(f"action '{action}' failed: {_scrub(str(e))}")
        return _sentence(
            f"Something went wrong talking to the mail server ({_explain(e)}). "
            f"You can check the account details in Settings → Mail.")


TOOL = {
    "name": "email_agent",
    "description": (
        "Reads and sends real email over IMAP/SMTP for the account configured in "
        "Settings → Mail. Use for: checking the inbox or unread mail, searching "
        "messages by sender/subject/word/date, opening one message's full text, "
        "marking a message read, and sending a new mail or a reply. Use this "
        "INSTEAD of opening Gmail or Outlook in a browser when the user asks what "
        "is in their mail — it answers in one step and does not need a window. "
        "Do NOT use this for chat apps (use send_message) or for anything about "
        "the user's own computer."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "inbox", "search", "read", "mark_read", "send"],
                "description": ("What to do. inbox = recent messages, "
                                "search = filtered messages, read = one full "
                                "message, mark_read = flag a message read, "
                                "send = new mail or reply, status = is mail "
                                "connected."),
            },
            "unread": {
                "type": "BOOLEAN",
                "description": "Only unread messages (inbox/search).",
            },
            "from": {
                "type": "STRING",
                "description": "Sender name or address to match (search).",
            },
            "subject": {
                "type": "STRING",
                "description": "Words in the subject line (search).",
            },
            "contains": {
                "type": "STRING",
                "description": "Words anywhere in the message (search).",
            },
            "since": {
                "type": "STRING",
                "description": ("How far back to look, in words or a date: "
                                "'yesterday', '3 days ago', '2 weeks ago', "
                                "'2026-09-01' (search)."),
            },
            "number": {
                "type": "NUMBER",
                "description": ("Which message to open, counting from the newest "
                                "as 1 (read, mark_read)."),
            },
            "uid": {
                "type": "STRING",
                "description": "Exact message id from a previous listing (read, mark_read).",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address (send).",
            },
            "body": {
                "type": "STRING",
                "description": "The text of the message to send (send).",
            },
            "reply_to": {
                "type": "STRING",
                "description": ("uid of the message being replied to; the "
                                "recipient and 'Re:' subject are filled in "
                                "automatically (send)."),
            },
            "limit": {
                "type": "NUMBER",
                "description": f"Maximum messages to list (1-{_MAX_ROWS}). Default {_DEFAULT_ROWS}.",
            },
        },
        "required": ["action"],
    },
    "handler": email_agent,
}
