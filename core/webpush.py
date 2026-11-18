# Copyright © 2026 CapeNet Ltd. MIT licence; see LICENSE.
"""Web Push, by hand: how the hut sends a notification to a phone.

A browser that agrees to notifications hands the page a *subscription*: an
address at its maker's push service (Google's for Chrome and Android, Apple's for
an iPhone, Mozilla's for Firefox) and two keys. To notify that phone the hut
POSTs an encrypted message to that address, signed to say who is sending it. The
push service cannot read the message; it only carries it.

Two standards, both small, both implemented here rather than pulled in:

* **VAPID** (RFC 8292): a short-lived token, signed with the club's own key
  pair, in the ``Authorization`` header. The same public key is what the browser
  was given when it subscribed, which is how the push service knows this sender
  is the one the user said yes to.
* **Message encryption** (RFC 8291 over RFC 8188's ``aes128gcm``): a fresh key
  pair per message, an ECDH exchange with the browser's key, and AES-128-GCM.

It is written out, in the manner of ``core/r2.py``'s request signing, because the
alternative is a library that brings an HTTP client and its dependencies with it,
and this app talks to the outside world with ``urllib`` and nothing else. The
arithmetic comes from ``pycryptodomex``, which the off-site backup already
installs; without it nothing here works and ``available()`` says so.

``tests/test_webpush_crypto.py`` holds this to the worked example printed in
RFC 8291, byte for byte.

Nothing in this module knows about races, subscribers or settings. It is given a
subscription and a message and reports what the push service said.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

# Where a subscription's address may point. A subscribe request comes from the
# public internet, and its address is somewhere this PC will later POST to: left
# open, that is a way to make the hut send requests to any address anyone likes.
# These are the push services of the browsers there are.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",                   # Chrome, Android, most Chromium browsers
    "push.services.mozilla.com",            # Firefox (updates.push.services.mozilla.com)
    "push.apple.com",                       # Safari and iPhone (web.push.apple.com)
    "notify.windows.com",                   # Edge on Windows (wns2-*.notify.windows.com)
)

# The message body is one encryption record. Push services refuse a body over
# 4096 bytes; the record's own overhead is 103, and a notification is a title
# and a line of text, so this is generous and well inside it.
RECORD_SIZE = 4096
MAX_PAYLOAD_BYTES = 3000
VAPID_LIFETIME_S = 12 * 3600                # a token may be valid for at most 24 hours
DEFAULT_TIMEOUT_S = 10


class WebPushUnavailable(RuntimeError):
    """The cryptography this needs is not installed."""


def _crypto():
    try:
        from Cryptodome.Cipher import AES
        from Cryptodome.Hash import SHA256
        from Cryptodome.PublicKey import ECC
        from Cryptodome.Signature import DSS
    except Exception as exc:                # pragma: no cover - only without the package
        raise WebPushUnavailable("pycryptodomex is not installed") from exc
    return AES, SHA256, ECC, DSS


def available() -> bool:
    try:
        _crypto()
    except WebPushUnavailable:
        return False
    return True


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    text = str(text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point_bytes(key: Any) -> bytes:
    """A P-256 public key as the 65 bytes browsers exchange: 0x04, X, Y."""
    point = key.pointQ
    return b"\x04" + int(point.x).to_bytes(32, "big") + int(point.y).to_bytes(32, "big")


def _public_key_from_bytes(raw: bytes) -> Any:
    _aes, _sha, ECC, _dss = _crypto()
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("not an uncompressed P-256 public key")
    return ECC.construct(curve="P-256", point_x=int.from_bytes(raw[1:33], "big"),
                         point_y=int.from_bytes(raw[33:], "big"))


def _private_key_from_bytes(raw: bytes) -> Any:
    _aes, _sha, ECC, _dss = _crypto()
    return ECC.construct(curve="P-256", d=int.from_bytes(raw, "big"))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


# --------------------------------------------------------------------------- keys

def generate_vapid_keys() -> Tuple[str, str]:
    """A new key pair for the club: (private key as PEM, public key as base64url).

    The public half is what a browser is given when it subscribes, and every
    subscription is tied to it for good: lose the private key and every phone
    has to subscribe again.
    """
    _aes, _sha, ECC, _dss = _crypto()
    key = ECC.generate(curve="P-256")
    return key.export_key(format="PEM"), b64url_encode(_point_bytes(key))


def public_key_from_private(private_pem: str) -> str:
    _aes, _sha, ECC, _dss = _crypto()
    return b64url_encode(_point_bytes(ECC.import_key(private_pem)))


# --------------------------------------------------------------------------- VAPID

def push_origin(endpoint: str) -> str:
    """The scheme and host of a subscription's address: the token's audience."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def vapid_token(endpoint: str, private_pem: str, subject: str,
                now: Optional[float] = None, lifetime_s: int = VAPID_LIFETIME_S) -> str:
    """The signed token (a JWT, ES256) saying who is sending to this push service.

    ``subject`` is how the push service's operator could reach the sender: an
    ``https:`` address or a ``mailto:``. Apple's service refuses a token whose
    subject is neither, or has a space in it.
    """
    _aes, SHA256, ECC, DSS = _crypto()
    issued = int(now if now is not None else time.time())
    header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64url_encode(json.dumps(
        {"aud": push_origin(endpoint), "exp": issued + int(lifetime_s), "sub": subject},
        separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    # ES256 in a JWT is the two numbers r and s end to end, 64 bytes, which is
    # what "binary" (the default) gives. Not DER.
    signature = DSS.new(ECC.import_key(private_pem), "fips-186-3").sign(SHA256.new(signing_input))
    return f"{header}.{claims}.{b64url_encode(signature)}"


def vapid_authorization(endpoint: str, private_pem: str, public_key: str, subject: str,
                        now: Optional[float] = None) -> str:
    return f"vapid t={vapid_token(endpoint, private_pem, subject, now)}, k={public_key}"


# --------------------------------------------------------------------------- encryption

def encrypt_payload(plaintext: bytes, p256dh: str, auth: str, *,
                    salt: Optional[bytes] = None, sender_private: Optional[bytes] = None) -> bytes:
    """One message, encrypted for one subscription (RFC 8291, ``aes128gcm``).

    ``p256dh`` and ``auth`` are the subscription's two keys as the browser gives
    them. ``salt`` and ``sender_private`` are random and made here; they are
    parameters only so the RFC's worked example can be reproduced in a test.
    """
    AES, _sha, ECC, _dss = _crypto()
    if len(plaintext) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"a push message is at most {MAX_PAYLOAD_BYTES} bytes")
    receiver_public = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    if len(auth_secret) != 16:
        raise ValueError("the subscription's auth secret must be 16 bytes")
    receiver = _public_key_from_bytes(receiver_public)
    sender = _private_key_from_bytes(sender_private) if sender_private else ECC.generate(curve="P-256")
    sender_public = _point_bytes(sender)
    salt = salt if salt is not None else os.urandom(16)
    if len(salt) != 16:
        raise ValueError("the salt must be 16 bytes")

    # ECDH: the X coordinate of (our private number x their public point).
    shared = int((receiver.pointQ * sender.d).x).to_bytes(32, "big")
    # RFC 8291 section 3.4: mix in the auth secret and both public keys...
    key_info = b"WebPush: info\x00" + receiver_public + sender_public
    ikm = _hmac(_hmac(auth_secret, shared), key_info + b"\x01")
    # ...then RFC 8188's own derivation of the content key and the nonce.
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    # A single record, so it is the last: the plaintext is followed by 0x02.
    cipher = AES.new(cek, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext + b"\x02")
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public
    return header + ciphertext + tag


# --------------------------------------------------------------------------- sending

def endpoint_allowed(endpoint: str) -> bool:
    """Whether an address is a real browser push service, over HTTPS."""
    try:
        parts = urlsplit(str(endpoint or ""))
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if port not in (None, 443):
        return False
    return any(host == known or host.endswith("." + known) for known in PUSH_SERVICE_HOSTS)


@dataclass
class PushResult:
    """What the push service said about one message to one subscription."""
    status: int                 # the HTTP status, or 0 when there was no answer
    ok: bool
    gone: bool                  # the subscription no longer exists: forget it
    retry: bool                 # worth sending again later
    detail: str = ""


def send(subscription: Dict[str, Any], payload: bytes, *, private_pem: str, public_key: str,
         subject: str, ttl: int = 3600, urgency: str = "normal", topic: str = "",
         timeout: float = DEFAULT_TIMEOUT_S,
         opener: Callable[..., Any] = urllib.request.urlopen) -> PushResult:
    """POST one encrypted message to one subscription.

    ``subscription`` carries ``endpoint``, ``p256dh`` and ``auth``. ``ttl`` is how
    long the push service keeps the message for a phone that is off; ``topic``
    lets a later message with the same topic replace one not yet delivered.

    Only "this subscription is gone" (404 or 410) is reported as ``gone``. A 401
    or 403 means the push service did not accept *our* token, which is true of
    every subscription at once: treating that as "gone" would delete them all
    over a mistake of our own.
    """
    endpoint = str(subscription.get("endpoint") or "")
    if not endpoint_allowed(endpoint):
        return PushResult(0, False, True, False, "not a push service address")
    try:
        body = encrypt_payload(payload, str(subscription.get("p256dh") or ""),
                               str(subscription.get("auth") or ""))
    except ValueError as exc:
        # Keys no browser could have issued: the subscription can never work.
        return PushResult(0, False, True, False, f"bad subscription keys: {exc}")
    headers = {
        "Authorization": vapid_authorization(endpoint, private_pem, public_key, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(max(0, int(ttl))),
        "Urgency": urgency if urgency in ("very-low", "low", "normal", "high") else "normal",
    }
    if topic:
        headers["Topic"] = topic[:32]
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with opener(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            detail = exc.read(300).decode("utf-8", "replace").strip()
        except Exception:
            detail = ""
        return PushResult(status, False, status in (404, 410),
                          status == 429 or status >= 500, detail or str(exc.reason))
    except Exception as exc:        # no network, a timeout, a refused connection
        return PushResult(0, False, False, True, f"{type(exc).__name__}: {exc}")
    return PushResult(status, 200 <= status < 300, False, False, "")
