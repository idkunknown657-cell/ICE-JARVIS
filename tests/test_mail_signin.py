"""OAuth sign-in for mail — PKCE correctness, the loopback catcher, and the
token lifecycle.

These tests never touch the network and never touch the real config: the token
exchange is replaced with a recorder, and the config file is redirected to a
temporary directory. The parts that matter are the ones a wrong implementation
gets subtly wrong — the PKCE transform, the CSRF state check, whether a refresh
happens before or after the token expires, and whether a secret can reach a
sentence the user reads.
"""
import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from core import mail_auth as ma
from memory import config_manager as cm


class _Isolated(unittest.TestCase):
    """Redirect the config file so no test can write to the user's settings."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._patchers = [
            mock.patch.object(cm, "CONFIG_DIR", tmp),
            mock.patch.object(cm, "CONFIG_FILE", tmp / "api_keys.json"),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)


# ── PKCE ─────────────────────────────────────────────────────────────────────

class PkceTest(_Isolated):
    def test_challenge_matches_the_rfc7636_reference_vector(self):
        """The one published test vector there is. If this fails, no provider
        will accept the sign-in, and nothing local would reveal why."""
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(ma.make_challenge(verifier),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_verifier_is_within_the_spec_length_and_alphabet(self):
        for _ in range(50):
            v = ma.make_verifier()
            self.assertTrue(43 <= len(v) <= 128, len(v))
            self.assertNotIn("=", v)      # padding is not allowed
            self.assertNotIn("+", v)
            self.assertNotIn("/", v)

    def test_challenge_is_the_s256_of_the_verifier(self):
        v = ma.make_verifier()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(v.encode("ascii")).digest()).rstrip(b"=").decode()
        self.assertEqual(ma.make_challenge(v), expected)

    def test_state_is_random_per_call(self):
        self.assertNotEqual(ma.make_state(), ma.make_state())


# ── the authorize URL ────────────────────────────────────────────────────────

class AuthorizeUrlTest(_Isolated):
    def _params(self, provider, **kw):
        url = ma.authorize_url(provider, "cid", "http://127.0.0.1:9/",
                               "st", "ch", **kw)
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    def test_uses_the_authorization_code_flow_with_s256(self):
        q = self._params("gmail")
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["code_challenge"], ["ch"])
        self.assertEqual(q["state"], ["st"])

    def test_gmail_asks_for_a_refresh_token(self):
        """Without offline access Google hands back an access token that dies
        within the hour, so the user would be asked to sign in every day."""
        q = self._params("gmail")
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["prompt"], ["consent"])

    def test_microsoft_scope_includes_offline_access(self):
        q = self._params("outlook")
        self.assertIn("offline_access", q["scope"][0])
        self.assertEqual(q["response_mode"], ["query"])

    def test_login_hint_is_only_sent_when_known(self):
        self.assertNotIn("login_hint", self._params("gmail"))
        self.assertEqual(self._params("gmail", login_hint="a@b.com")["login_hint"],
                         ["a@b.com"])

    def test_redirect_uri_is_the_loopback_one_it_was_given(self):
        q = self._params("gmail")
        self.assertEqual(q["redirect_uri"], ["http://127.0.0.1:9/"])

    def test_every_provider_has_the_fields_the_flow_needs(self):
        for key, p in ma.PROVIDERS.items():
            for field in ("label", "auth_url", "token_url", "scope",
                          "imap_host", "smtp_host", "console", "console_hint"):
                self.assertTrue(p.get(field), f"{key}.{field}")


# ── the loopback catcher ─────────────────────────────────────────────────────

class LoopbackTest(_Isolated):
    def _hit(self, port, query):
        url = f"http://127.0.0.1:{port}/?{query}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode("utf-8")

    def test_captures_the_code_and_shows_a_connected_page(self):
        with ma._SignInServer("good") as s:
            self.assertGreater(s.port, 0)          # an ephemeral port, not a fixed one
            body = self._hit(s.port, "state=good&code=one-time")
            self.assertTrue(s.wait(2))
            self.assertEqual(s.code, "one-time")
            self.assertIn("connected", body.lower())

    def test_a_code_with_the_wrong_state_is_refused(self):
        """The CSRF guard: a code that does not match the state we generated
        belongs to a flow somebody else started."""
        with ma._SignInServer("good") as s:
            self._hit(s.port, "state=attacker&code=stolen")
            self.assertTrue(s.wait(2))
            self.assertEqual(s.code, "")
            self.assertEqual(s.error, "state mismatch")

    def test_a_cancelled_sign_in_is_reported_as_cancelled(self):
        with ma._SignInServer("good") as s:
            self._hit(s.port, "error=access_denied")
            self.assertTrue(s.wait(2))
            self.assertEqual(s.error, "access_denied")

    def test_a_stray_request_does_not_end_the_flow(self):
        """Browsers fetch favicons and reload pages. If an unrelated request
        finished the flow, the real redirect would arrive at a closed server."""
        with ma._SignInServer("good") as s:
            self._hit(s.port, "")                     # a bare request
            self.assertFalse(s.wait(0.3))             # still listening
            self.assertEqual(s.code, "")
            self._hit(s.port, "state=good&code=real")  # the real one still lands
            self.assertTrue(s.wait(2))
            self.assertEqual(s.code, "real")

    def test_the_server_closes_with_the_flow(self):
        with ma._SignInServer("good") as s:
            port = s.port
            s.close()
        # A closed listener refuses connections rather than accepting one more.
        with self.assertRaises(Exception):
            self._hit(port, "state=good&code=late")

    def test_the_failure_page_escapes_what_it_echoes(self):
        """The rejection reason comes from the query string, so it is attacker
        controlled text being written into HTML."""
        with ma._SignInServer("good") as s:
            body = self._hit(s.port, "error=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
            self.assertTrue(s.wait(2))
            self.assertNotIn("<script>", body)


# ── the whole flow ───────────────────────────────────────────────────────────

class SignInFlowTest(_Isolated):
    def _run(self, provider="gmail", *, token_reply=None, cancel=False,
             state_tweak=None, whoami="me@example.com"):
        """Drive a sign-in, standing in for both the browser and the provider."""
        sent = {}
        reply = token_reply if token_reply is not None else {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600}

        def fake_post(url, fields):
            sent.clear()
            sent.update(fields)
            sent["_url"] = url
            if reply.get("__error"):
                # Route the raw provider error through the real mapper, so this
                # stub cannot be more forgiving than the code it replaces.
                return {}, ma._oauth_error_detail(
                    json.dumps({"error": reply["__error"]}))
            return dict(reply), ""

        patchers = [
            mock.patch.object(ma, "_post_form", fake_post),
            mock.patch.object(ma, "_whoami", lambda *_: whoami),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

        result = {}

        def on_open(url):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            redirect, state = q["redirect_uri"][0], q["state"][0]
            result["url"] = url

            def hop():
                if cancel:
                    return
                st = state_tweak if state_tweak is not None else state
                try:
                    urllib.request.urlopen(
                        f"{redirect}?state={urllib.parse.quote(st)}&code=code-1",
                        timeout=5).read()
                except Exception:
                    pass

            threading.Thread(target=hop, daemon=True).start()

        signin = ma.SignIn(provider, "client-123.apps.googleusercontent.com",
                           whoami or "")
        first = signin.start(on_open=on_open)
        snap = first
        for _ in range(200):
            snap = signin.snapshot()
            if snap["phase"] in ("connected", "failed", "cancelled"):
                break
            time.sleep(0.02)
        return signin, first, snap, sent, result

    # -- happy path --
    def test_a_complete_sign_in_connects_and_stores_the_account(self):
        _s, first, snap, sent, opened = self._run()
        self.assertTrue(opened.get("url"), "the sign-in URL must exist up front")
        self.assertIn("accounts.google.com", first["url"])
        self.assertEqual(snap["phase"], "connected")
        self.assertEqual(sent["grant_type"], "authorization_code")
        self.assertTrue(sent["code_verifier"])
        self.assertEqual(sent["code"], "code-1")
        self.assertEqual(sent["redirect_uri"][:17], "http://127.0.0.1:")

        cfg = cm.get_mail_config()
        self.assertEqual(cfg["auth"], "oauth")
        self.assertEqual(cfg["address"], "me@example.com")
        self.assertEqual(cfg["imap_host"], "imap.gmail.com")   # from the preset

    def test_the_first_snapshot_already_carries_the_url(self):
        """The panel has to show the link the moment it appears, in case the
        browser never opened."""
        _s, first, _snap, _sent, _o = self._run()
        self.assertTrue(first["url"].startswith("https://"))

    def test_a_wrong_state_never_reaches_the_token_endpoint(self):
        _s, _first, snap, sent, _o = self._run(state_tweak="not-the-state")
        self.assertEqual(snap["phase"], "failed")
        self.assertEqual(sent, {})          # nothing was exchanged
        self.assertEqual(cm.get_mail_config(), {})

    def test_cancelling_stops_the_flow_and_stores_nothing(self):
        signin, _first, _snap, _sent, _o = self._run(cancel=True)
        time.sleep(0.1)
        signin.cancel()
        for _ in range(100):
            if signin.snapshot()["phase"] == "cancelled":
                break
            time.sleep(0.02)
        self.assertEqual(signin.snapshot()["phase"], "cancelled")
        self.assertEqual(cm.get_mail_config(), {})

    def test_a_provider_without_a_refresh_token_is_a_failure_not_a_half_account(self):
        """A sign-in that would silently expire within the hour is worse than a
        refusal, because it looks like it worked."""
        _s, _f, snap, _sent, _o = self._run(
            token_reply={"access_token": "at", "expires_in": 3600})
        self.assertEqual(snap["phase"], "failed")
        self.assertIn("every hour", snap["message"])
        self.assertEqual(cm.get_mail_config(), {})

    def test_a_token_endpoint_error_is_surfaced_plainly(self):
        _s, _f, snap, _sent, _o = self._run(
            token_reply={"__error": "invalid_grant"})
        self.assertEqual(snap["phase"], "failed")
        self.assertIn("expired", snap["message"].lower())

    def test_a_rejected_client_id_says_which_field_to_check(self):
        _s, _f, snap, _sent, _o = self._run(
            token_reply={"__error": "invalid_client"})
        self.assertEqual(snap["phase"], "failed")
        self.assertIn("client ID", snap["message"])

    def test_a_loopback_redirect_rejection_names_the_cause(self):
        """This is the failure a Web-application client ID produces, and it is
        the one a user is least likely to guess."""
        _s, _f, snap, _sent, _o = self._run(
            token_reply={"__error": "redirect_uri_mismatch"})
        self.assertIn("Desktop-app", snap["message"])

    def test_a_missing_client_id_fails_before_opening_anything(self):
        signin = ma.SignIn("gmail", "", "me@example.com")
        snap = signin.start(on_open=lambda url: self.fail("must not open a page"))
        self.assertEqual(snap["phase"], "failed")
        self.assertIn("client ID", snap["message"])

    def test_an_email_pasted_into_the_client_id_field_is_caught_early(self):
        signin = ma.SignIn("gmail", "me@example.com", "")
        snap = signin.start()
        self.assertEqual(snap["phase"], "failed")
        self.assertIn("does not look like a client ID", snap["message"])

    def test_an_unknown_provider_is_refused(self):
        snap = ma.SignIn("aol", "cid", "").start()
        self.assertEqual(snap["phase"], "failed")


# ── tokens ───────────────────────────────────────────────────────────────────

class TokenTest(_Isolated):
    def _stored(self, expires_in=3600, **kw):
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt", access_token="at",
                           expires_at=time.time() + expires_in,
                           address="me@example.com", **kw)

    def test_a_valid_token_is_used_without_asking_again(self):
        self._stored()
        with mock.patch.object(ma, "_post_form",
                               side_effect=AssertionError("should not refresh")):
            token, err = ma.access_token()
        self.assertEqual((token, err), ("at", ""))

    def test_a_token_close_to_expiry_is_refreshed_before_it_is_used(self):
        """The leeway exists so a token cannot die mid-conversation, which would
        surface as an authentication failure in the middle of reading mail."""
        self._stored(expires_in=30)
        calls = []

        def fake_post(url, fields):
            calls.append(fields)
            return {"access_token": "fresh", "expires_in": 3600}, ""

        with mock.patch.object(ma, "_post_form", fake_post):
            token, err = ma.access_token()
        self.assertEqual((token, err), ("fresh", ""))
        self.assertEqual(calls[0]["grant_type"], "refresh_token")
        self.assertEqual(calls[0]["refresh_token"], "rt")

    def test_a_refresh_is_persisted_so_it_is_not_repeated(self):
        self._stored(expires_in=30)
        with mock.patch.object(ma, "_post_form",
                               return_value=({"access_token": "fresh",
                                              "expires_in": 3600}, "")):
            ma.access_token()
        self.assertEqual(cm.get_mail_oauth()["access_token"], "fresh")
        self.assertGreater(cm.get_mail_oauth()["expires_at"], time.time() + 3000)

    def test_a_rotated_refresh_token_replaces_the_old_one(self):
        """Google may issue a new refresh token on refresh. Keeping the stale one
        would break the account the next time the access token expired."""
        self._stored(expires_in=30)
        with mock.patch.object(ma, "_post_form",
                               return_value=({"access_token": "fresh",
                                              "refresh_token": "rt-2",
                                              "expires_in": 3600}, "")):
            ma.access_token()
        self.assertEqual(cm.get_mail_oauth()["refresh_token"], "rt-2")

    def test_a_refresh_failure_returns_a_sentence_rather_than_raising(self):
        self._stored(expires_in=30)
        with mock.patch.object(ma, "_post_form",
                               return_value=({}, "the server refused it")):
            token, err = ma.access_token()
        self.assertEqual(token, "")
        self.assertIn("refused", err)

    def test_no_account_means_no_token_and_no_exception(self):
        token, err = ma.access_token()
        self.assertEqual(token, "")
        self.assertTrue(err)

    def test_an_expired_access_token_without_a_refresh_token_reads_as_signed_out(self):
        """`get_mail_oauth` promises a usable credential or nothing. An
        access-token-only block cannot be used an hour from now, so it must not
        read as signed in."""
        cm._patch_config(mail={"oauth": {"provider": "gmail",
                                         "access_token": "orphan"}})
        self.assertEqual(cm.get_mail_oauth(), {})
        self.assertEqual(ma.access_token()[0], "")


# ── secrets ──────────────────────────────────────────────────────────────────

class SecretTest(_Isolated):
    def test_every_stored_secret_is_scrubbed_from_output(self):
        cm.save_mail_oauth(provider="gmail", client_id="client-abcdefghijklmnop",
                           refresh_token="refresh-abcdefghijklmnop",
                           access_token="access-abcdefghijklmnop",
                           expires_at=time.time() + 60, address="me@example.com")
        cm.save_mail_config(provider="gmail", address="me@example.com",
                            password="app-password-here")
        text = ma.scrub("client-abcdefghijklmnop refresh-abcdefghijklmnop "
                        "access-abcdefghijklmnop app-password-here")
        for secret in ("client-abcdefghijklmnop", "refresh-abcdefghijklmnop",
                       "access-abcdefghijklmnop", "app-password-here"):
            self.assertNotIn(secret, text)
        self.assertIn("********", text)

    def test_saving_the_password_form_does_not_destroy_a_saved_sign_in(self):
        """The bug this test exists for: retyping an address revoked the
        token, which is unrecoverable from the settings screen and reads to the
        user as the sign-in having been forgotten."""
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt-keep", access_token="at",
                           expires_at=time.time() + 3600, address="me@example.com")
        cm.save_mail_config(provider="gmail", address="me@example.com",
                            password="pw")
        self.assertEqual(cm.get_mail_oauth().get("refresh_token"), "rt-keep")
        # …and the password the user just typed is the credential now in use.
        self.assertEqual(cm.get_mail_config()["auth"], "password")

    def test_signing_in_after_an_app_password_switches_back_to_the_token(self):
        cm.save_mail_config(provider="gmail", address="me@example.com",
                            password="pw")
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt", access_token="at",
                           expires_at=time.time() + 3600,
                           address="me@example.com")
        cfg = cm.get_mail_config()
        self.assertEqual(cfg["auth"], "oauth")
        self.assertTrue(cm.mail_signed_in())

    def test_re_saving_with_an_empty_password_keeps_both_credentials(self):
        """"An empty box keeps the stored secret" has to stay true now that a
        token can live beside it — and the token has to survive too."""
        cm.save_mail_config(provider="gmail", address="me@example.com",
                            password="pw")
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt", access_token="at",
                           expires_at=time.time() + 3600,
                           address="me@example.com")
        cm.save_mail_config(provider="gmail", address="me@example.com",
                            password="")            # empty keeps the saved one
        self.assertEqual(cm.get_mail_config()["auth"], "password")
        self.assertEqual(cm.get_mail_config()["password"], "pw")
        self.assertEqual(cm.get_mail_oauth()["refresh_token"], "rt")

    def test_a_short_value_is_left_alone(self):
        """Masking a two-character string would mangle ordinary prose in every
        error message that happened to contain it."""
        cm.save_mail_config(provider="custom", address="me@example.com",
                            password="ab")
        self.assertEqual(ma.scrub("ab is not a secret here"),
                         "ab is not a secret here")

    def test_the_error_strings_never_carry_a_token(self):
        self.assertEqual(ma._explain_url_error(Exception("no internet")),
                         "no internet")

    def test_a_tls_failure_is_described_instead_of_quoted(self):
        msg = ma._explain_url_error(Exception("SSL: CERTIFICATE_VERIFY_FAILED"))
        self.assertIn("secure connection failed", msg)

    def test_a_dns_failure_says_so(self):
        msg = ma._explain_url_error(Exception("getaddrinfo failed"))
        self.assertIn("could not be found", msg)

    def test_the_scrubbed_sentence_shows_nothing_from_an_oauth_error_body(self):
        """A provider's error body is free to quote the credential that failed."""
        detail = ma._oauth_error_detail(
            json.dumps({"error": "invalid_client",
                        "error_description": "client secret abc123 leaked"}))
        self.assertIn("client ID was rejected", detail)


# ── the SASL string ──────────────────────────────────────────────────────────

class Xoauth2Test(_Isolated):
    def test_the_string_has_the_two_trailing_nuls_sasl_requires(self):
        s = ma.xoauth2_string("me@example.com", "tok")
        self.assertEqual(s, "user=me@example.com\x01auth=Bearer tok\x01\x01")

    def test_the_address_and_token_both_appear_once(self):
        s = ma.xoauth2_string("a@b.com", "ABC")
        self.assertEqual(s.count("a@b.com"), 1)
        self.assertEqual(s.count("ABC"), 1)


# ── address resolution ───────────────────────────────────────────────────────

class AddressTest(_Isolated):
    def test_a_known_address_is_returned_without_a_call(self):
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt", access_token="at",
                           expires_at=time.time() + 3600,
                           address="known@example.com")
        with mock.patch.object(ma, "_whoami",
                               side_effect=AssertionError("must not ask")):
            self.assertEqual(ma.resolve_address()[0], "known@example.com")

    def test_an_unknown_address_is_discovered_and_persisted(self):
        """XOAUTH2 needs the address in the SASL string, so a sign-in that never
        learned it cannot log in until it is resolved."""
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt", access_token="at",
                           expires_at=time.time() + 3600, address="")
        with mock.patch.object(ma, "_whoami", lambda *_: "found@example.com"):
            self.assertEqual(ma.resolve_address()[0], "found@example.com")
        self.assertEqual(cm.get_mail_oauth()["address"], "found@example.com")
        self.assertEqual(cm.get_mail_config()["address"], "found@example.com")

    def test_still_unknown_is_an_error_not_an_empty_string(self):
        cm.save_mail_oauth(provider="gmail", client_id="cid",
                           refresh_token="rt", access_token="at",
                           expires_at=time.time() + 3600, address="")
        with mock.patch.object(ma, "_whoami", lambda *_: ""):
            address, err = ma.resolve_address()
        self.assertEqual(address, "")
        self.assertTrue(err)

    def test_no_sign_in_is_an_error(self):
        address, err = ma.resolve_address()
        self.assertEqual(address, "")
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
