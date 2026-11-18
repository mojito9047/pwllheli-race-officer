"""The notification the hut sends is one a phone can actually read.

Web Push is two published standards, and ``core/webpush.py`` implements both by
hand rather than through a library. Hand-written cryptography is only defensible
if it is held to something outside itself, so the first test here is the worked
example printed in RFC 8291 (section 5 and appendix A): the same keys, the same
salt, the same sentence, and the message that comes out must be the RFC's, byte
for byte. A browser decrypts by the same rules, so a message that matches the
example is one a browser can open.

The rest covers what the RFC's example cannot: the signed token that tells the
push service who is sending (checked by verifying it with the public key, which
is what the push service does), what is refused before anything is sent, and how
each kind of answer from a push service is read -- above all that an answer
meaning "your token is wrong" is never taken to mean "this phone has gone".
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from core import webpush

pytest.importorskip("Cryptodome", reason="pycryptodomex does the arithmetic")

# RFC 8291, section 5 and appendix A.
PLAINTEXT = b"When I grow up, I want to be a watermelon"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
AUTH_SECRET = "BTBZMqHH6r4Tts7J_aSIgg"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
AS_PUBLIC = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
MESSAGE = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
           "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
           "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def decrypt(body: bytes, receiver_private_b64: str, auth_b64: str) -> bytes:
    """What a browser does with a message, written separately from the code that
    made it: read the header, redo the exchange from the *receiving* side."""
    import hashlib
    import hmac
    from Cryptodome.Cipher import AES
    from Cryptodome.PublicKey import ECC

    def mac(key, data):
        return hmac.new(key, data, hashlib.sha256).digest()

    salt, key_len = body[:16], body[20]
    sender_public = body[21:21 + key_len]
    ciphertext = body[21 + key_len:]
    receiver = ECC.construct(curve="P-256", d=int.from_bytes(webpush.b64url_decode(receiver_private_b64), "big"))
    receiver_public = (b"\x04" + int(receiver.pointQ.x).to_bytes(32, "big")
                       + int(receiver.pointQ.y).to_bytes(32, "big"))
    sender = ECC.construct(curve="P-256", point_x=int.from_bytes(sender_public[1:33], "big"),
                           point_y=int.from_bytes(sender_public[33:], "big"))
    shared = int((sender.pointQ * receiver.d).x).to_bytes(32, "big")
    ikm = mac(mac(webpush.b64url_decode(auth_b64), shared),
              b"WebPush: info\x00" + receiver_public + sender_public + b"\x01")
    prk = mac(salt, ikm)
    cek = mac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = mac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    padded = AES.new(cek, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext[:-16], ciphertext[-16:])
    assert padded.endswith(b"\x02"), "a single record ends with the last-record delimiter"
    return padded[:-1]


class TestTheWorkedExampleInTheStandard:
    def test_the_message_is_the_rfcs_byte_for_byte(self):
        body = webpush.encrypt_payload(PLAINTEXT, UA_PUBLIC, AUTH_SECRET,
                                       salt=webpush.b64url_decode(SALT),
                                       sender_private=webpush.b64url_decode(AS_PRIVATE))
        assert webpush.b64url_encode(body) == MESSAGE

    def test_the_header_names_the_senders_public_key(self):
        body = webpush.encrypt_payload(PLAINTEXT, UA_PUBLIC, AUTH_SECRET,
                                       salt=webpush.b64url_decode(SALT),
                                       sender_private=webpush.b64url_decode(AS_PRIVATE))
        assert body[:16] == webpush.b64url_decode(SALT)
        assert int.from_bytes(body[16:20], "big") == 4096
        assert body[20] == 65 and body[21:86] == webpush.b64url_decode(AS_PUBLIC)

    def test_the_receiver_can_read_it(self):
        body = webpush.b64url_decode(MESSAGE)
        assert decrypt(body, UA_PRIVATE, AUTH_SECRET) == PLAINTEXT


class TestEveryMessageIsItsOwn:
    def test_a_fresh_key_and_salt_each_time(self):
        """The same notice to the same phone twice must not be the same bytes."""
        one = webpush.encrypt_payload(b"Race started", UA_PUBLIC, AUTH_SECRET)
        two = webpush.encrypt_payload(b"Race started", UA_PUBLIC, AUTH_SECRET)
        assert one != two
        assert decrypt(one, UA_PRIVATE, AUTH_SECRET) == b"Race started"
        assert decrypt(two, UA_PRIVATE, AUTH_SECRET) == b"Race started"

    def test_a_notification_with_real_text_in_it(self):
        text = json.dumps({"title": "Ras Enlli", "body": "SGRECH BACH finished 14:02:10 — 1st"}).encode()
        assert decrypt(webpush.encrypt_payload(text, UA_PUBLIC, AUTH_SECRET), UA_PRIVATE, AUTH_SECRET) == text

    def test_a_message_too_long_for_one_record_is_refused(self):
        with pytest.raises(ValueError):
            webpush.encrypt_payload(b"x" * (webpush.MAX_PAYLOAD_BYTES + 1), UA_PUBLIC, AUTH_SECRET)

    def test_keys_no_browser_issued_are_refused(self):
        with pytest.raises(ValueError):
            webpush.encrypt_payload(b"hello", "AAAA", AUTH_SECRET)
        with pytest.raises(ValueError):
            webpush.encrypt_payload(b"hello", UA_PUBLIC, "AAAA")


class TestTheSendersToken:
    def _keys(self):
        return webpush.generate_vapid_keys()

    def test_a_new_key_pair_is_the_size_browsers_expect(self):
        private_pem, public = self._keys()
        assert "PRIVATE KEY" in private_pem
        raw = webpush.b64url_decode(public)
        assert len(raw) == 65 and raw[0] == 4
        assert webpush.public_key_from_private(private_pem) == public

    def test_the_push_service_can_verify_it_with_the_public_key(self):
        from Cryptodome.Hash import SHA256
        from Cryptodome.PublicKey import ECC
        from Cryptodome.Signature import DSS
        private_pem, public = self._keys()
        token = webpush.vapid_token("https://fcm.googleapis.com/fcm/send/abc", private_pem,
                                    "https://pro.example.org", now=1_800_000_000)
        header, claims, signature = token.split(".")
        raw = webpush.b64url_decode(public)
        key = ECC.construct(curve="P-256", point_x=int.from_bytes(raw[1:33], "big"),
                            point_y=int.from_bytes(raw[33:], "big"))
        sig = webpush.b64url_decode(signature)
        assert len(sig) == 64, "ES256 in a token is r and s end to end, not DER"
        DSS.new(key, "fips-186-3").verify(SHA256.new(f"{header}.{claims}".encode()), sig)   # raises if wrong

    def test_it_says_who_for_how_long_and_from_whom(self):
        private_pem, _public = self._keys()
        token = webpush.vapid_token("https://web.push.apple.com/QOn...", private_pem,
                                    "https://pro.example.org", now=1_800_000_000)
        header, claims, _sig = (json.loads(webpush.b64url_decode(part)) if i < 2 else part
                                for i, part in enumerate(token.split(".")))
        assert header == {"typ": "JWT", "alg": "ES256"}
        assert claims["aud"] == "https://web.push.apple.com"
        assert claims["sub"] == "https://pro.example.org"
        assert 0 < claims["exp"] - 1_800_000_000 <= 24 * 3600, "a push service refuses a token valid for over a day"

    def test_the_header_carries_the_token_and_the_key(self):
        private_pem, public = self._keys()
        value = webpush.vapid_authorization("https://fcm.googleapis.com/fcm/send/abc", private_pem,
                                            public, "https://pro.example.org")
        assert value.startswith("vapid t=") and value.endswith(f", k={public}")


class TestWhereTheHutWillSendTo:
    """A subscribe request arrives from the public internet carrying an address
    this PC will later POST to."""

    def test_the_browsers_own_push_services(self):
        for endpoint in ("https://fcm.googleapis.com/fcm/send/abc:def",
                         "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
                         "https://web.push.apple.com/QOnW8Yb",
                         "https://wns2-par02p.notify.windows.com/w/?token=BQYAAA"):
            assert webpush.endpoint_allowed(endpoint), endpoint

    def test_nothing_else(self):
        for endpoint in ("http://fcm.googleapis.com/fcm/send/abc",        # not https
                         "https://example.com/push",
                         "https://fcm.googleapis.com.evil.example/x",       # a look-alike suffix
                         "https://evilfcm.googleapis.com.example/x",
                         "https://192.168.1.1/admin",
                         "https://localhost/x",
                         "https://user:pass@fcm.googleapis.com/fcm/send/abc",
                         "https://fcm.googleapis.com:8443/fcm/send/abc",
                         "ftp://fcm.googleapis.com/x", "", None, "not a url"):
            assert not webpush.endpoint_allowed(endpoint), endpoint


class _Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


def _http_error(code):
    return urllib.error.HTTPError("https://fcm.googleapis.com/x", code, "refused", {}, io.BytesIO(b"why"))


class TestReadingThePushServicesAnswer:
    SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": UA_PUBLIC, "auth": AUTH_SECRET}

    def _send(self, opener):
        private_pem, public = webpush.generate_vapid_keys()
        return webpush.send(self.SUB, b'{"title":"t"}', private_pem=private_pem, public_key=public,
                            subject="https://pro.example.org", opener=opener)

    def test_what_is_posted(self):
        seen = {}

        def opener(request, timeout=None):
            seen["url"] = request.full_url
            seen["headers"] = {k.lower(): v for k, v in request.header_items()}
            seen["body"] = request.data
            seen["method"] = request.get_method()
            return _Answer(201)

        result = self._send(opener)
        assert result.ok and result.status == 201 and not result.gone and not result.retry
        assert seen["url"] == self.SUB["endpoint"] and seen["method"] == "POST"
        assert seen["headers"]["content-encoding"] == "aes128gcm"
        assert seen["headers"]["ttl"] == "3600"
        assert seen["headers"]["authorization"].startswith("vapid t=")
        assert decrypt(seen["body"], UA_PRIVATE, AUTH_SECRET) == b'{"title":"t"}'

    def test_gone_means_forget_this_phone(self):
        for code in (404, 410):
            def opener(request, timeout=None, code=code):
                raise _http_error(code)
            result = self._send(opener)
            assert result.gone and not result.ok and not result.retry, code

    def test_our_own_token_being_refused_is_not_the_phone_having_gone(self):
        """401 and 403 are about the sender, and so about every subscription at
        once. Read as "gone", one mistake of ours would delete them all."""
        for code in (400, 401, 403, 413):
            def opener(request, timeout=None, code=code):
                raise _http_error(code)
            result = self._send(opener)
            assert not result.gone and not result.ok and not result.retry, code
            assert result.detail == "why"

    def test_busy_or_broken_means_try_again(self):
        for code in (429, 500, 502, 503):
            def opener(request, timeout=None, code=code):
                raise _http_error(code)
            result = self._send(opener)
            assert result.retry and not result.gone, code

    def test_no_network_means_try_again(self):
        def opener(request, timeout=None):
            raise TimeoutError("timed out")
        result = self._send(opener)
        assert result.retry and result.status == 0 and "TimeoutError" in result.detail

    def test_an_address_that_is_not_a_push_service_is_never_posted_to(self):
        called = []

        def opener(request, timeout=None):
            called.append(request.full_url)
            return _Answer(201)

        private_pem, public = webpush.generate_vapid_keys()
        result = webpush.send({**self.SUB, "endpoint": "https://192.168.1.1/admin"}, b"{}",
                              private_pem=private_pem, public_key=public,
                              subject="https://pro.example.org", opener=opener)
        assert not called and result.gone, "it can never work, so it is dropped"

    def test_a_subscription_with_bad_keys_is_dropped_not_retried(self):
        private_pem, public = webpush.generate_vapid_keys()
        result = webpush.send({**self.SUB, "p256dh": "AAAA"}, b"{}", private_pem=private_pem,
                              public_key=public, subject="https://pro.example.org",
                              opener=lambda *a, **k: _Answer(201))
        assert result.gone and not result.retry
