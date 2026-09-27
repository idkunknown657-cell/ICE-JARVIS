"""Unit tests for actions/email_agent.py — real IMAP/SMTP mail.

SAFETY: no test opens a network connection and none touches the user's real
config. The IMAP and SMTP servers are fakes, and the account block is patched
into the module rather than written to api_keys.json — a suite that can
overwrite the file holding the user's credentials is a suite that gets deleted.

The assertions that matter are the security ones, so they are stated as such:
that a password never appears in any returned sentence, that reading a message
does not mark it read, and that nothing is sent without confirmation.
"""
import email
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import email_agent as ea
from memory import config_manager as cm


ACCOUNT = {
    "provider": "gmail",
    "address": "user@example.com",
    "password": "app-password-1234",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
}


def _msg(subject="Hello", sender="Ada Lovelace <ada@example.com>",
         body="The message body.", date="Mon, 21 Sep 2026 10:00:00 +0000",
         html=False, mime="text/plain"):
    m = email.message.EmailMessage()
    m["From"] = sender
    m["To"] = "user@example.com"
    m["Subject"] = subject
    m["Date"] = date
    m["Message-ID"] = "<abc123@example.com>"
    m.set_content(body) if not html else m.set_content(body, subtype="html")
    return m


class FakeIMAP:
    """A scripted IMAP server: enough of the protocol to test the client's
    behaviour, including the mistakes a real server makes."""

    def __init__(self, messages=None, login_error=None, folders=("INBOX",),
                 fail_select=False):
        # messages: list of (uid, EmailMessage, flags)
        self.messages = messages if messages is not None else []
        self.login_error = login_error
        self.folders = list(folders)
        self.fail_select = fail_select
        self.calls = []           # ("uid", "FETCH", ...) etc — what was asked
        self.selected_readonly = None
        self.stored = []

    # -- lifecycle --
    def login(self, user, password):
        self.calls.append(("login", user))
        if self.login_error:
            raise self.login_error
        return ("OK", [b"logged in"])

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        self.selected_readonly = readonly
        if self.fail_select:
            return ("NO", [b"cannot open"])
        return ("OK", [str(len(self.messages)).encode()])

    def close(self):
        self.calls.append(("close",))

    def logout(self):
        self.calls.append(("logout",))

    def uid(self, command, *args):
        self.calls.append(("uid", command) + tuple(args))
        cmd = command.upper()
        if cmd == "SEARCH":
            criteria = " ".join(str(a) for a in args).upper()
            uids = []
            for uid, msg, flags in self.messages:
                if "UNSEEN" in criteria and "\\Seen" in flags:
                    continue
                uids.append(uid.encode())
            return ("OK", [b" ".join(uids)])
        if cmd == "FETCH":
            target = str(args[0])
            spec = str(args[1])
            for uid, msg, flags in self.messages:
                if uid != target:
                    continue
                if "BODY.PEEK[HEADER]" in spec:
                    raw = msg.as_bytes()
                    # header-only, like a real server
                    raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                else:
                    raw = msg.as_bytes()
                # A real FETCH response item is a 3-tuple: the header line,
                # the payload, then the flag line. `_fetch_one`/`_fetch_headers`
                # read the flags from the third element.
                trailer = f" FLAGS ({flags})".encode() if flags else b""
                return ("OK", [(b"1 (UID " + uid.encode(), raw, trailer)])
            return ("OK", [None])
        if cmd == "STORE":
            self.stored.append(args)
            for i, (uid, msg, flags) in enumerate(self.messages):
                if uid == str(args[0]):
                    self.messages[i] = (uid, msg, "\\Seen")
            return ("OK", [b"stored"])
        return ("OK", [None])


def _with_account(fake, fn, account=None):
    """Run `fn` with a patched account and a patched IMAP4_SSL."""
    acct = ACCOUNT if account is None else account
    with mock.patch.object(ea, "get_mail_config", return_value=dict(acct)), \
         mock.patch.object(ea.imaplib, "IMAP4_SSL", return_value=fake):
        return fn()


class NoAccountTest(unittest.TestCase):
    def test_unconfigured_account_explains_itself(self):
        with mock.patch.object(ea, "get_mail_config", return_value={}):
            out = ea.email_agent({"action": "inbox"})
        self.assertIn("Settings", out)
        self.assertIn("app password", out.lower())

    def test_missing_password_is_named(self):
        acct = dict(ACCOUNT, password="")
        with mock.patch.object(ea, "get_mail_config", return_value=acct):
            out = ea.email_agent({"action": "status"})
        self.assertIn("password", out.lower())

    def test_missing_imap_host_is_named(self):
        acct = dict(ACCOUNT, imap_host="")
        with mock.patch.object(ea, "get_mail_config", return_value=acct):
            out = ea.email_agent({"action": "status"})
        self.assertIn("IMAP", out)

    def test_config_reader_never_raises_on_junk(self):
        for junk in (None, "text", 42, {"provider": None, "imap_port": "x"}):
            with mock.patch.object(cm, "load_api_keys", return_value={"mail": junk}):
                cfg = cm.get_mail_config()
            self.assertIsInstance(cfg, dict)


class SecurityTest(unittest.TestCase):
    def test_the_password_never_appears_in_a_failure_sentence(self):
        """A library is free to quote the command it failed on. If that text is
        passed through, the password ends up in a log file the user later
        shares — so every failure path is scrubbed."""
        boom = Exception(
            "login failed for LOGIN user@example.com app-password-1234")
        fake = FakeIMAP(login_error=boom)
        out = _with_account(fake, lambda: ea.email_agent({"action": "inbox"}))
        self.assertNotIn("app-password-1234", out)
        self.assertIn("********", out)

    def test_scrub_leaves_short_values_alone(self):
        # A 2-character password would otherwise mangle ordinary words.
        with mock.patch.object(ea, "get_mail_config",
                               return_value=dict(ACCOUNT, password="ab")):
            self.assertEqual(ea._scrub("about"), "about")

    def test_reading_a_message_uses_peek_not_a_flag_write(self):
        fake = FakeIMAP([("1", _msg(), "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "uid": "1"}))
        fetches = [c for c in fake.calls if c[:2] == ("uid", "FETCH")]
        self.assertTrue(fetches)
        self.assertIn("BODY.PEEK[]", fetches[0][3])
        # and the mailbox was opened read-only
        self.assertTrue(fake.selected_readonly)
        self.assertIn("app-password-1234", "app-password-1234")  # sanity
        self.assertNotIn("app-password-1234", out)

    def test_inbox_lists_headers_only(self):
        fake = FakeIMAP([("1", _msg(body="SECRET-BODY-TEXT"), "")])
        _with_account(fake, lambda: ea.email_agent({"action": "inbox"}))
        fetches = [c for c in fake.calls if c[:2] == ("uid", "FETCH")]
        self.assertTrue(fetches)
        self.assertIn("BODY.PEEK[HEADER]", fetches[0][3])


class AuthFailureTest(unittest.TestCase):
    def test_a_rejected_login_suggests_an_app_password(self):
        import imaplib as _imaplib
        fake = FakeIMAP(login_error=_imaplib.IMAP4.error(
            b"[AUTHENTICATIONFAILED] Invalid credentials"))
        out = _with_account(fake, lambda: ea.email_agent({"action": "inbox"}))
        self.assertIn("app password", out.lower())
        self.assertNotIn("app-password-1234", out)

    def test_an_unreachable_server_is_explained_not_thrown(self):
        def boom(*a, **k):
            raise OSError("[Errno 11001] getaddrinfo failed")
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)), \
             mock.patch.object(ea.imaplib, "IMAP4_SSL", side_effect=boom):
            out = ea.email_agent({"action": "inbox"})
        self.assertIn("could not be found", out.lower())

    def test_a_timeout_is_explained(self):
        def boom(*a, **k):
            raise TimeoutError("timed out")
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)), \
             mock.patch.object(ea.imaplib, "IMAP4_SSL", side_effect=boom):
            out = ea.email_agent({"action": "inbox"})
        self.assertIn("timed out", out.lower())


class InboxTest(unittest.TestCase):
    def test_three_messages_are_listed_newest_first(self):
        msgs = [("1", _msg(subject="Oldest"), "\\Seen"),
                ("2", _msg(subject="Middle"), ""),
                ("3", _msg(subject="Newest"), "")]
        fake = FakeIMAP(msgs)
        out = _with_account(fake, lambda: ea.email_agent({"action": "inbox"}))
        self.assertIn("Newest", out)
        self.assertLess(out.index("Newest"), out.index("Middle"))
        self.assertLess(out.index("Middle"), out.index("Oldest"))
        self.assertIn("2 unread", out)

    def test_unread_only_filters_read_messages(self):
        msgs = [("1", _msg(subject="AlreadyRead"), "\\Seen"),
                ("2", _msg(subject="StillUnread"), "")]
        fake = FakeIMAP(msgs)
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "inbox", "unread": True}))
        self.assertIn("StillUnread", out)
        self.assertNotIn("AlreadyRead", out)

    def test_an_empty_inbox_says_so_plainly(self):
        fake = FakeIMAP([])
        out = _with_account(fake, lambda: ea.email_agent({"action": "inbox"}))
        self.assertIn("no messages matching", out.lower())

    def test_nothing_unread_is_not_an_error(self):
        fake = FakeIMAP([("1", _msg(), "\\Seen")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "inbox", "unread": True}))
        self.assertIn("no unread", out.lower())

    def test_the_limit_is_clamped_not_trusted(self):
        msgs = [(str(i), _msg(subject=f"Msg {i}"), "") for i in range(1, 30)]
        fake = FakeIMAP(msgs)
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "inbox", "limit": 500}))
        # The cap protects what can be spoken aloud, so it must hold.
        self.assertLessEqual(out.count("\n"), ea._MAX_ROWS + 2)

    def test_zero_or_junk_limit_falls_back(self):
        self.assertEqual(ea._limit({"limit": 0}), 1)
        self.assertEqual(ea._limit({"limit": "abc"}), ea._DEFAULT_ROWS)
        self.assertEqual(ea._limit({"limit": -3}), 1)

    def test_a_folder_that_will_not_open_is_reported(self):
        fake = FakeIMAP([("1", _msg(), "")], fail_select=True)
        out = _with_account(fake, lambda: ea.email_agent({"action": "inbox"}))
        self.assertIn("could not open", out.lower())


class SearchTest(unittest.TestCase):
    def _criteria(self, fake):
        for c in fake.calls:
            if c[:2] == ("uid", "SEARCH"):
                return " ".join(str(x) for x in c[2:])
        return ""

    def test_terms_are_built_and_quoted(self):
        fake = FakeIMAP([("1", _msg(subject="Invoice"), "")])
        _with_account(fake, lambda: ea.email_agent(
            {"action": "search", "from": "ada@example.com"}))
        crit = self._criteria(fake)
        self.assertIn("FROM", crit)
        self.assertIn('"ada@example.com"', crit)

    def test_a_quote_inside_a_term_is_escaped(self):
        # Otherwise a subject with a quote in it changes the search's meaning.
        self.assertEqual(ea._quote('say "hi"'), '"say \\"hi\\""')

    def test_a_backslash_is_escaped(self):
        self.assertEqual(ea._quote("a\\b"), '"a\\\\b"')

    def test_searching_with_nothing_to_search_for_asks(self):
        fake = FakeIMAP([])
        out = _with_account(fake, lambda: ea.email_agent({"action": "search"}))
        self.assertIn("tell me what to search", out.lower())
        self.assertEqual([c for c in fake.calls if c[:2] == ("uid", "SEARCH")], [])

    def test_since_accepts_relative_and_absolute(self):
        fake = FakeIMAP([])
        _with_account(fake, lambda: ea.email_agent(
            {"action": "search", "since": "3 days ago"}))
        self.assertIn("SINCE", self._criteria(fake))

    def test_an_unreadable_date_is_refused_not_guessed(self):
        fake = FakeIMAP([])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "search", "since": "sometime last spring"}))
        self.assertIn("could not read", out.lower())
        # and no search ran with a guessed date
        self.assertEqual([c for c in fake.calls if c[:2] == ("uid", "SEARCH")], [])

    def test_imap_date_formats(self):
        self.assertRegex(ea._imap_date("today"), r"^\d{2}-[A-Z][a-z]{2}-\d{4}$")
        self.assertRegex(ea._imap_date("2026-09-01"), r"^\d{2}-[A-Z][a-z]{2}-\d{4}$")
        self.assertIsNone(ea._imap_date("the other day"))
        self.assertIsNone(ea._imap_date(""))


class ReadTest(unittest.TestCase):
    def test_a_message_body_is_returned(self):
        fake = FakeIMAP([("1", _msg(subject="Hello", body="Meet at five."), "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "uid": "1"}))
        self.assertIn("Meet at five.", out)
        self.assertIn("Hello", out)
        self.assertIn("Ada Lovelace", out)

    def test_numbering_is_newest_first(self):
        msgs = [("1", _msg(subject="Oldest"), ""),
                ("2", _msg(subject="Middle"), ""),
                ("3", _msg(subject="Newest"), "")]
        fake = FakeIMAP(msgs)
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "number": 1}))
        self.assertIn("Newest", out)
        self.assertNotIn("Oldest", out)

    def test_a_number_beyond_the_inbox_says_how_many_exist(self):
        fake = FakeIMAP([("1", _msg(), "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "number": 9}))
        self.assertIn("only 1", out)

    def test_html_only_mail_is_de_tagged(self):
        """No HTML is ever spoken aloud."""
        html = ("<html><body><p>Hello <b>there</b></p>"
                "<script>var x = 1;</script><p>Bye&nbsp;now</p></body></html>")
        fake = FakeIMAP([("1", _msg(body=html, html=True), "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "uid": "1"}))
        self.assertIn("Hello", out)
        self.assertIn("there", out)
        self.assertIn("Bye now", out)
        self.assertNotIn("<b>", out)
        self.assertNotIn("var x", out)

    def test_a_long_body_says_it_was_cut(self):
        long_body = "word " * 900
        fake = FakeIMAP([("1", _msg(body=long_body), "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "uid": "1"}))
        self.assertIn("cut off", out)

    def test_a_message_with_no_readable_body_says_so(self):
        m = email.message.EmailMessage()
        m["From"] = "ada@example.com"
        m["Subject"] = "Attachment only"
        m["Date"] = "Mon, 21 Sep 2026 10:00:00 +0000"
        m.set_content("")
        fake = FakeIMAP([("1", m, "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "read", "uid": "1"}))
        self.assertIn("no readable text body", out.lower())

    def test_mark_read_stores_the_seen_flag(self):
        fake = FakeIMAP([("1", _msg(), "")])
        out = _with_account(fake, lambda: ea.email_agent(
            {"action": "mark_read", "uid": "1"}))
        self.assertEqual(out, "Marked as read.")
        stores = [c for c in fake.calls if c[:2] == ("uid", "STORE")]
        self.assertTrue(stores)
        self.assertIn("\\Seen", " ".join(str(x) for x in stores[0]))


class HeadersTest(unittest.TestCase):
    def test_encoded_subjects_are_decoded(self):
        m = _msg()
        m.replace_header("Subject", "=?utf-8?B?SGVsbG8g4pyT?=")
        self.assertEqual(ea._hdr(m.get("Subject")), "Hello ✓")

    def test_a_broken_header_does_not_raise(self):
        self.assertEqual(ea._hdr("=??garbage??="), "=??garbage??=")
        self.assertEqual(ea._hdr(None), "")

    def test_ago_wording(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        self.assertEqual(ea._ago(now), "just now")
        self.assertIn("min ago", ea._ago(now - timedelta(minutes=5)))
        self.assertIn("h ago", ea._ago(now - timedelta(hours=3)))
        self.assertIn("days ago", ea._ago(now - timedelta(days=3)))
        self.assertEqual(ea._ago(None), "unknown date")

    def test_the_sender_name_is_preferred_over_the_address(self):
        rows = ea._fetch_headers(FakeIMAP([("1", _msg(
            sender="Ada Lovelace <ada@example.com>"), "")]), ["1"])
        self.assertEqual(rows[0]["from"], "Ada Lovelace")
        self.assertEqual(rows[0]["address"], "ada@example.com")


class SendTest(unittest.TestCase):
    def test_sending_asks_for_confirmation_and_does_not_send_yet(self):
        sent = []
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)), \
             mock.patch.object(ea, "_deliver_now",
                               side_effect=lambda *a, **k: sent.append(a) or "Sent."), \
             mock.patch("core.confirm.request",
                        return_value="[CONFIRMATION_PENDING] ask the user") as gate:
            out = ea.email_agent({"action": "send", "to": "ada@example.com",
                                  "subject": "Hi", "body": "Hello"})
        self.assertEqual(sent, [])                        # nothing went out
        self.assertTrue(gate.called)
        args = gate.call_args[0]
        self.assertIn("ada@example.com", args[1])         # the title names the recipient
        self.assertIn("Hello", args[2])                   # the detail shows the text
        self.assertEqual(out, "[CONFIRMATION_PENDING] ask the user")

    def test_a_missing_recipient_asks_rather_than_sending(self):
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)):
            out = ea.email_agent({"action": "send", "body": "Hi"})
        self.assertIn("who should i send", out.lower())

    def test_a_non_address_recipient_is_refused(self):
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)):
            out = ea.email_agent({"action": "send", "to": "my brother",
                                  "body": "Hi"})
        self.assertIn("does not look like an email address", out.lower())

    def test_an_empty_body_asks_rather_than_sending_blank_mail(self):
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)):
            out = ea.email_agent({"action": "send", "to": "ada@example.com"})
        self.assertIn("what should the message say", out.lower())

    def test_the_message_is_built_and_handed_to_smtp(self):
        delivered = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                delivered["host"] = host
                delivered["port"] = port

            def ehlo(self): pass
            def starttls(self, context=None): delivered["tls"] = True
            def login(self, user, password):
                delivered["user"] = user
            def send_message(self, msg):
                delivered["msg"] = msg
            def quit(self): pass

        with mock.patch.object(ea.smtplib, "SMTP", FakeSMTP):
            out = ea._deliver_now(dict(ACCOUNT), "ada@example.com",
                                  "Hi", "Hello there")
        self.assertEqual(out, "Sent to ada@example.com.")
        self.assertTrue(delivered["tls"])                 # STARTTLS on 587
        self.assertEqual(delivered["host"], "smtp.gmail.com")
        msg = delivered["msg"]
        self.assertEqual(msg["To"], "ada@example.com")
        self.assertEqual(msg["From"], "user@example.com")
        self.assertEqual(msg["Subject"], "Hi")
        self.assertIn("Hello there", msg.get_content())

    def test_port_465_uses_implicit_tls(self):
        used = {}

        class FakeSMTPSSL:
            def __init__(self, host, port, timeout=None, context=None):
                used["ssl"] = True
            def login(self, u, p): pass
            def send_message(self, m): pass
            def quit(self): pass

        with mock.patch.object(ea.smtplib, "SMTP_SSL", FakeSMTPSSL):
            out = ea._deliver_now(dict(ACCOUNT, smtp_port=465),
                                  "ada@example.com", "Hi", "Hello")
        self.assertTrue(used.get("ssl"))
        self.assertIn("Sent to", out)

    def test_a_send_failure_says_nothing_was_sent(self):
        class BrokenSMTP:
            def __init__(self, *a, **k):
                raise OSError("network is unreachable")

        with mock.patch.object(ea.smtplib, "SMTP", BrokenSMTP):
            out = ea._deliver_now(dict(ACCOUNT), "ada@example.com", "Hi", "Hello")
        self.assertIn("could not reach", out.lower())
        self.assertNotIn("app-password-1234", out)

    def test_auth_failure_on_send_never_echoes_the_password(self):
        import smtplib as _smtplib

        class RejectingSMTP:
            def __init__(self, *a, **k): pass
            def ehlo(self): pass
            def starttls(self, context=None): pass
            def login(self, u, p):
                raise _smtplib.SMTPAuthenticationError(
                    535, b"bad credentials app-password-1234")
            def quit(self): pass

        with mock.patch.object(ea.smtplib, "SMTP", RejectingSMTP):
            out = ea._deliver_now(dict(ACCOUNT), "ada@example.com", "Hi", "Hello")
        self.assertIn("nothing was sent", out.lower())
        self.assertNotIn("app-password-1234", out)

    def test_reply_fills_in_recipient_and_subject(self):
        fake = FakeIMAP([("7", _msg(subject="Question",
                                    sender="ada@example.com"), "")])
        seen = {}
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)), \
             mock.patch.object(ea.imaplib, "IMAP4_SSL", return_value=fake), \
             mock.patch.object(ea, "_deliver_now",
                               side_effect=lambda *a, **k: seen.update(
                                   to=a[1], subject=a[2], quoted=a[4]) or "Sent."), \
             mock.patch("core.confirm.request",
                        side_effect=lambda key, title, detail, run: run()):
            out = ea.email_agent({"action": "send", "reply_to": "7",
                                  "body": "Answer"})
        self.assertEqual(seen["to"], "ada@example.com")
        self.assertEqual(seen["subject"], "Re: Question")
        self.assertEqual(seen["quoted"]["in_reply_to"], "<abc123@example.com>")
        self.assertEqual(out, "Sent.")


class RoutingTest(unittest.TestCase):
    def test_synonyms_route_to_the_right_action(self):
        cases = {"check": "inbox", "find": "search", "open": "read",
                 "reply": "send", "mark": "mark_read", "connect": "status"}
        for word, expected in cases.items():
            with mock.patch.dict(ea._HANDLERS, {expected: lambda *a, **k: expected}):
                out = ea.email_agent({"action": word})
            self.assertEqual(out, expected, f"{word} should route to {expected}")

    def test_a_bare_send_shaped_request_routes_to_send(self):
        with mock.patch.dict(ea._HANDLERS, {"send": lambda *a, **k: "send"}):
            out = ea.email_agent({"to": "a@b.com", "body": "hi"})
        self.assertEqual(out, "send")

    def test_a_bare_search_shaped_request_routes_to_search(self):
        with mock.patch.dict(ea._HANDLERS, {"search": lambda *a, **k: "search"}):
            out = ea.email_agent({"from": "ada"})
        self.assertEqual(out, "search")

    def test_nothing_at_all_still_answers_with_the_inbox(self):
        fake = FakeIMAP([("1", _msg(subject="Ping"), "")])
        out = _with_account(fake, lambda: ea.email_agent({}))
        self.assertIn("Ping", out)

    def test_a_reply_with_no_text_asks_for_it(self):
        with mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)):
            out = ea.email_agent({"action": "send", "reply_to": "3"})
        self.assertIn("what should i reply with", out.lower())

    def test_a_crashing_handler_returns_a_sentence_not_a_traceback(self):
        def boom(*a, **k):
            raise RuntimeError("kaboom")
        with mock.patch.dict(ea._HANDLERS, {"inbox": boom}), \
             mock.patch.object(ea, "get_mail_config", return_value=dict(ACCOUNT)):
            out = ea.email_agent({"action": "inbox"})
        self.assertIsInstance(out, str)
        self.assertIn("kaboom", out)
        self.assertNotIn("Traceback", out)


class ConfigTest(unittest.TestCase):
    """The mail settings round-trip, including the rule that matters most: an
    empty password must not erase the saved one."""

    def setUp(self):
        self.saved = {}
        self._patch = mock.patch.object(
            cm, "_patch_config",
            side_effect=lambda **kw: self.saved.update(kw))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_presets_cover_the_providers_people_use(self):
        for key in ("gmail", "outlook", "yahoo", "icloud", "custom"):
            self.assertIn(key, cm.MAIL_PRESETS)
        self.assertEqual(cm.MAIL_PRESETS["gmail"]["imap_host"], "imap.gmail.com")
        self.assertEqual(cm.MAIL_PRESETS["gmail"]["smtp_port"], 587)

    def test_saving_picks_up_the_preset_hosts(self):
        cm.save_mail_config("gmail", "user@example.com", "pw")
        mail = self.saved["mail"]
        self.assertEqual(mail["imap_host"], "imap.gmail.com")
        self.assertEqual(mail["smtp_host"], "smtp.gmail.com")
        self.assertEqual(mail["address"], "user@example.com")

    def test_an_empty_password_keeps_the_saved_one(self):
        with mock.patch.object(cm, "load_api_keys",
                               return_value={"mail": dict(ACCOUNT)}):
            cm.save_mail_config("gmail", "user@example.com", "")
        self.assertEqual(self.saved["mail"]["password"], ACCOUNT["password"])

    def test_a_new_password_replaces_the_old_one(self):
        with mock.patch.object(cm, "load_api_keys",
                               return_value={"mail": dict(ACCOUNT)}):
            cm.save_mail_config("gmail", "user@example.com", "newsecret")
        self.assertEqual(self.saved["mail"]["password"], "newsecret")

    def test_an_unknown_provider_becomes_custom_but_keeps_the_hosts(self):
        cm.save_mail_config("hotmail", "user@example.com", "pw",
                            imap_host="imap.hot.example", smtp_host="smtp.hot.example")
        mail = self.saved["mail"]
        self.assertEqual(mail["provider"], "custom")
        self.assertEqual(mail["imap_host"], "imap.hot.example")

    def test_a_junk_port_falls_back_to_the_preset(self):
        cm.save_mail_config("gmail", "u@e.com", "pw", imap_port="not a port")
        self.assertEqual(self.saved["mail"]["imap_port"], 993)

    def test_clear_wipes_the_block_including_the_password(self):
        cm.clear_mail_config()
        self.assertEqual(self.saved["mail"], {})

    def test_a_corrupt_block_reads_as_unconfigured(self):
        with mock.patch.object(cm, "load_api_keys",
                               return_value={"mail": ["not", "a", "dict"]}):
            self.assertEqual(cm.get_mail_config(), {})


class ToolDeclarationTest(unittest.TestCase):
    def test_the_declaration_is_well_formed(self):
        tool = ea.TOOL
        self.assertEqual(tool["name"], "email_agent")
        self.assertEqual(tool["parameters"]["type"], "OBJECT")
        self.assertIn("action", tool["parameters"]["required"])
        self.assertTrue(callable(tool["handler"]))
        self.assertTrue(tool["description"].strip())

    def test_every_enum_value_has_a_handler(self):
        for action in ea.TOOL["parameters"]["properties"]["action"]["enum"]:
            self.assertIn(action, ea._HANDLERS)

    def test_every_declared_parameter_is_read_somewhere(self):
        """A declared parameter nothing reads is a promise the tool does not
        keep — the model will set it and nothing will happen."""
        src = Path(ea.__file__).read_text(encoding="utf-8")
        for name in ea.TOOL["parameters"]["properties"]:
            self.assertIn(f'"{name}"', src, f"{name} is declared but never read")

    def test_the_description_points_away_from_the_browser(self):
        desc = ea.TOOL["description"].lower()
        self.assertIn("instead of opening gmail", desc)


if __name__ == "__main__":
    unittest.main()
