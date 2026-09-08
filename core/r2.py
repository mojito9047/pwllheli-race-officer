"""Cloudflare R2 (S3-compatible) request signing and the object verbs the app uses.

This is the one AWS Signature V4 implementation in the app. It exists because the
off-site backup needs more of the S3 surface than publishing a video does: a
backup is PUT, then verified with HEAD, and old ones are found with a LIST and
removed with a DELETE. Writing a second signer for that would have meant two
places to get the canonical-request rules wrong, so core/video.py's signed
uploader now calls in here too and the rules live in one function.

The upload-allowance constants also live here rather than in core/video.py,
because they are a fact about the hut's connection and not about video: the hut
is on 4G shared with a caravan park, and anything pushed from it — a race clip or
a backup ZIP — needs the same size-derived patience.

Nothing in this module reads app settings or touches the database, so it is safe
to import from anywhere in core/ without an import cycle. Callers pass the
account, bucket and keys they want to use; the off-site backup deliberately uses
a different bucket from the public video one.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from werkzeug.utils import secure_filename

# How long an upload is allowed to take.
#
# It used to be a flat 60 s for the built-in uploader and 90 s for the curl
# fallback, which is fine on a desk and hopeless from the hut: a race clip is
# tens of megabytes and the hut is on 4G shared with a caravan park. On a
# Saturday evening a start video simply could not be pushed inside 90 seconds,
# and every attempt failed with a write timeout having uploaded most of the file.
#
# So the allowance is worked out from the size of the thing being sent and a
# floor on throughput. The important part is the *stall* test underneath it: curl
# is told to give up only if the transfer drops below R2_STALL_BYTES_PER_S for
# R2_STALL_SECONDS together, which separates "slow but working" — leave it alone
# — from "dead", which should fail quickly rather than sit out the whole
# allowance. The overall timeout is only a backstop for a curl that hangs
# without transferring at all.
R2_MIN_BYTES_PER_S = 20_000        # ~160 kbit/s: the slowest link worth waiting for
R2_UPLOAD_BASE_TIMEOUT_S = 120.0   # connect, sign, and small objects
R2_UPLOAD_MAX_TIMEOUT_S = 3600.0   # an hour is past the point of usefulness
R2_STALL_BYTES_PER_S = 8_000       # under this...
R2_STALL_SECONDS = 60              # ...for this long, and the link is gone
R2_CONNECT_TIMEOUT_S = 20
# Listing, HEAD and DELETE move nothing, so they get a short flat allowance.
R2_METADATA_TIMEOUT_S = 60.0

EMPTY_PAYLOAD_SHA256 = hashlib.sha256(b"").hexdigest()


def r2_upload_timeout_s(size_bytes: int) -> float:
    """How long to allow for pushing ``size_bytes`` to R2.

    A 20 MB clip over a link managing 20 kB/s needs about seventeen minutes, and
    being cut off at ninety seconds helps nobody. Bounded so a wedged transfer
    cannot hold the upload lock for ever.
    """
    try:
        size = max(0, int(size_bytes))
    except (TypeError, ValueError):
        size = 0
    return min(R2_UPLOAD_MAX_TIMEOUT_S,
               R2_UPLOAD_BASE_TIMEOUT_S + size / float(R2_MIN_BYTES_PER_S))


# ---------------------------------------------------------------------------
# Naming rules. These validate what a user typed into Settings, and they live
# here because both the video bucket and the backup bucket are held to them.
# ---------------------------------------------------------------------------

def safe_r2_account_id(value: str) -> str:
    """Normalise a Cloudflare account ID for the R2 S3 endpoint.

    The Settings UI accepts either the bare account ID or the copied R2 S3
    endpoint URL, for example ``https://<account>.r2.cloudflarestorage.com``.
    Accepting both avoids a common setup mistake when copying values from the
    Cloudflare dashboard.
    """
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        parsed = urlparse(text)
        host = parsed.netloc
    else:
        host = text.split("/", 1)[0]
    host = host.strip().lower()
    suffix = ".r2.cloudflarestorage.com"
    if host.endswith(suffix):
        host = host[:-len(suffix)]
    elif host.endswith(".r2.dev"):
        # The public r2.dev domain is not an S3 API endpoint, but accepting the
        # account-label form gives the user a clearer validation path.
        host = host.split(".", 1)[0]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host if re.fullmatch(r"[a-z0-9]{16,64}", host) else ""


def safe_r2_bucket_name(value: str) -> str:
    """Normalise an R2 bucket name for path-style S3 requests."""
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", value) else ""


def safe_r2_key_prefix(value: str) -> str:
    """Return a safe slash-separated R2 object prefix."""
    parts = []
    for raw in str(value or "").replace("\\", "/").split("/"):
        part = secure_filename(raw.strip())
        if part:
            parts.append(part)
    return "/".join(parts)


def signing_key(secret_key: str, date_stamp: str) -> bytes:
    """Return the AWS Signature V4 signing key for R2's S3-compatible API."""
    k_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def endpoint_host(account_id: str) -> str:
    """Return the R2 S3 API host for an account ID."""
    account_id = str(account_id or "").strip()
    return f"{account_id}.r2.cloudflarestorage.com" if account_id else ""


def quote_key(key: str) -> str:
    """Percent-encode an object key one path segment at a time."""
    return "/".join(quote(part, safe="") for part in str(key).split("/"))


def canonical_query(query: Optional[Dict[str, str]]) -> str:
    """Return the sorted, encoded canonical query string SigV4 signs.

    Sorted by encoded key — a ListObjectsV2 call sends several parameters and R2
    rejects the signature if their order here differs from the sort AWS specifies.
    """
    if not query:
        return ""
    pairs = sorted((quote(str(k), safe="~"), quote(str(v), safe="~")) for k, v in query.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


# Winsock's "connection aborted by the software in your host machine". Seen on the
# hut uploading a backup: the peer answered and closed while urllib was still
# writing the body, so the local stack aborted the socket and the HTTP response —
# which is where R2 says *why* — was never read. The bare error reads like a flaky
# link and sends you to the router, which is the wrong place to look.
_ABORTED_MIDWAY_HINT = (
    " An established connection dropping partway through an upload usually means the far end "
    "rejected the request and closed it before the whole body was sent, rather than a broken "
    "link: most often the R2 API token does not cover this bucket, or the bucket does not exist. "
    "Check the token's bucket scope in the Cloudflare dashboard. A genuinely dropped 4G link "
    "looks the same from here, so it is worth re-running once."
)
_ABORT_ERRNOS = (10053, 10054, 32, 104)     # WSAECONNABORTED/RESET, EPIPE, ECONNRESET


def error_message(exc: BaseException) -> str:
    """Return a useful, non-secret R2 error message.

    The response body matters: R2 explains a refused request in it
    (``SignatureDoesNotMatch``, ``NoSuchBucket``), and without it the caller only
    sees "HTTP 403" and has nothing to act on.
    """
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        detail = f"HTTP {exc.code} {exc.reason}".strip()
        if body:
            detail += f": {body[:700]}"
        return detail
    if isinstance(exc, urllib.error.URLError):
        message = f"Network error: {exc.reason}"
        if looks_like_aborted_upload(exc.reason):
            message += _ABORTED_MIDWAY_HINT
        return message
    if looks_like_aborted_upload(exc):
        return f"Network error: {exc}{_ABORTED_MIDWAY_HINT}"
    return str(exc)


def looks_like_aborted_upload(reason: Any) -> bool:
    """Return whether an error is a connection torn down mid-transfer."""
    errno = getattr(reason, "errno", None) or getattr(reason, "winerror", None)
    if errno in _ABORT_ERRNOS:
        return True
    text = str(reason).lower()
    return any(marker in text for marker in ("10053", "10054", "connection was aborted",
                                             "connection reset", "broken pipe"))


def signed_request(
    method: str,
    account_id: str,
    bucket: str,
    key: str,
    access_key: str,
    secret_key: str,
    body: bytes = b"",
    query: Optional[Dict[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> Tuple[int, Dict[str, str], bytes]:
    """Send one signed request to R2 and return (status, headers, body).

    ``extra_headers`` are both sent and signed, which is what R2 requires for
    Content-Type and Cache-Control on a PUT. Header names are lower-cased and
    sorted for the canonical request because SigV4 is defined that way, not as a
    tidiness measure — the signature does not verify otherwise.

    Raises RuntimeError with a readable message on any failure, so callers do not
    have to know that the transport is urllib.
    """
    account_id = str(account_id or "").strip()
    bucket = str(bucket or "").strip()
    access_key = str(access_key or "").strip()
    secret_key = str(secret_key or "")
    if not (account_id and bucket and access_key and secret_key):
        raise ValueError("Cloudflare R2 account ID/endpoint, bucket, access key ID and secret access key are required.")
    host = endpoint_host(account_id)
    key = str(key or "").lstrip("/")
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_PAYLOAD_SHA256

    # A bucket-level request (a listing) has no key, and its canonical URI is the
    # bucket alone.
    path_parts = [bucket, *[part for part in key.split("/") if part]]
    canonical_uri = "/" + "/".join(quote(part, safe="") for part in path_parts)

    header_values = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    for name, value in (extra_headers or {}).items():
        header_values[str(name).strip().lower()] = str(value).strip()
    signed_names = sorted(header_values)
    canonical_headers = "".join(f"{name}:{header_values[name]}\n" for name in signed_names)
    signed_headers = ";".join(signed_names)
    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_query(query),
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(signing_key(secret_key, date_stamp), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    request_headers = {
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "Host": host,
        "X-Amz-Content-SHA256": payload_hash,
        "X-Amz-Date": amz_date,
    }
    for name, value in (extra_headers or {}).items():
        request_headers[str(name).strip()] = str(value).strip()
    if method.upper() == "PUT":
        request_headers["Content-Length"] = str(len(body))

    encoded_query = canonical_query(query)
    url = f"https://{host}{canonical_uri}" + (f"?{encoded_query}" if encoded_query else "")
    request = urllib.request.Request(url, data=body or None, method=method.upper(), headers=request_headers)
    allowance = timeout if timeout is not None else r2_upload_timeout_s(len(body))
    try:
        with urllib.request.urlopen(request, timeout=allowance) as response:
            return int(response.status), {k.lower(): v for k, v in response.headers.items()}, response.read()
    except Exception as exc:
        raise RuntimeError(error_message(exc)) from exc


def put_object(
    account_id: str,
    bucket: str,
    key: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    content_type: str = "application/octet-stream",
    cache_control: str = "no-store",
) -> None:
    """Upload one object, raising RuntimeError if R2 does not accept it."""
    if not str(key or "").strip():
        raise ValueError("Cloudflare R2 object key is empty.")
    status, _headers, _body = signed_request(
        "PUT", account_id, bucket, key, access_key, secret_key,
        body=body,
        extra_headers={"Cache-Control": cache_control, "Content-Type": content_type},
    )
    if status not in (200, 201, 204):
        raise RuntimeError(f"Cloudflare R2 upload returned HTTP {status}.")


def put_file_with_curl(
    account_id: str,
    bucket: str,
    key: str,
    path: Path,
    access_key: str,
    secret_key: str,
    content_type: str = "application/octet-stream",
    cache_control: str = "private, no-store",
) -> None:
    """Upload a file to R2 by streaming it through curl.

    Same flags and reasoning as the public-video uploader in core/video.py: give up
    on a dead link rather than a slow one, because the hut's 4G is genuinely slow
    on a Saturday evening and the overall timeout is only a backstop for a curl
    that hangs without transferring at all.

    curl streams straight from the file, so a backup archive is never held in
    memory — the built-in uploader has to read the whole thing in to sign it.
    """
    curl_path = shutil.which("curl") or shutil.which("curl.exe")
    if not curl_path:
        raise RuntimeError("curl was not found for the streaming upload.")
    account_id = safe_r2_account_id(account_id) or str(account_id or "").strip()
    bucket = str(bucket or "").strip()
    if not (account_id and bucket and access_key and secret_key):
        raise ValueError("Cloudflare R2 account ID/endpoint, bucket, access key ID and secret access key are required.")
    size_bytes = path.stat().st_size
    url = f"https://{endpoint_host(account_id)}/{quote(bucket, safe='')}/{quote_key(str(key).lstrip('/'))}"
    allowance = r2_upload_timeout_s(size_bytes)
    cmd = [
        curl_path,
        "--fail",
        "--silent",
        "--show-error",
        "--request", "PUT",
        "--upload-file", str(path),
        "--header", f"Content-Type: {content_type}",
        "--header", f"Cache-Control: {cache_control}",
        "--aws-sigv4", "aws:amz:auto:s3",
        "--user", f"{access_key}:{secret_key}",
        "--connect-timeout", str(R2_CONNECT_TIMEOUT_S),
        # Give up on a dead link, not on a slow one.
        "--speed-limit", str(R2_STALL_BYTES_PER_S),
        "--speed-time", str(R2_STALL_SECONDS),
        url,
    ]
    # Past the allowance curl is not transferring, or it would have finished; the
    # margin covers its own shutdown.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=allowance + 30)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"curl exited with {proc.returncode}").strip()
        raise RuntimeError(detail[:700])


def put_file(
    account_id: str,
    bucket: str,
    key: str,
    path: Path,
    access_key: str,
    secret_key: str,
    content_type: str = "application/octet-stream",
    cache_control: str = "private, no-store",
) -> str:
    """Upload a file to R2, streaming through curl when it is available.

    curl first, not second. The video uploader signs in-process and only falls back
    to curl, which is the right order for a small live JPEG; a backup archive is
    tens of megabytes and the in-process path has to hold all of it in memory and
    has no stall detection. Returns which method succeeded, for the status page.
    """
    errors = []
    if shutil.which("curl") or shutil.which("curl.exe"):
        try:
            put_file_with_curl(account_id, bucket, key, path, access_key, secret_key,
                               content_type=content_type, cache_control=cache_control)
            return "curl"
        except Exception as exc:
            errors.append(f"curl upload failed: {exc}")
    try:
        put_object(account_id, bucket, key, path.read_bytes(), access_key, secret_key,
                   content_type=content_type, cache_control=cache_control)
        return "built-in"
    except Exception as exc:
        errors.append(f"built-in upload failed: {exc}")
    raise RuntimeError(" | ".join(errors))


def head_object(account_id: str, bucket: str, key: str, access_key: str, secret_key: str) -> Dict[str, Any]:
    """Return size and ETag for a stored object.

    This is how an upload is checked rather than assumed. An S3 PUT is atomic, so
    an object that answers HEAD with the right length is the whole object.
    """
    _status, headers, _body = signed_request(
        "HEAD", account_id, bucket, key, access_key, secret_key,
        timeout=R2_METADATA_TIMEOUT_S,
    )
    try:
        size = int(headers.get("content-length") or 0)
    except (TypeError, ValueError):
        size = 0
    return {
        "size_bytes": size,
        "etag": (headers.get("etag") or "").strip('"'),
        "last_modified": headers.get("last-modified") or "",
    }


def delete_object(account_id: str, bucket: str, key: str, access_key: str, secret_key: str) -> None:
    """Remove one object. A key that is already gone is not an error."""
    status, _headers, _body = signed_request(
        "DELETE", account_id, bucket, key, access_key, secret_key,
        timeout=R2_METADATA_TIMEOUT_S,
    )
    if status not in (200, 202, 204, 404):
        raise RuntimeError(f"Cloudflare R2 delete returned HTTP {status}.")


# The S3 ListObjectsV2 response namespace. ElementTree reports tags fully
# qualified, so it has to be matched rather than ignored.
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def parse_list_objects(xml_body: bytes) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse a ListObjectsV2 body into objects and the next continuation token."""
    objects: List[Dict[str, Any]] = []
    root = ET.fromstring(xml_body)
    for contents in root.findall(f"{_S3_NS}Contents"):
        key_el = contents.find(f"{_S3_NS}Key")
        size_el = contents.find(f"{_S3_NS}Size")
        modified_el = contents.find(f"{_S3_NS}LastModified")
        if key_el is None or not (key_el.text or "").strip():
            continue
        try:
            size = int((size_el.text or "0").strip()) if size_el is not None else 0
        except (TypeError, ValueError):
            size = 0
        objects.append({
            "key": key_el.text.strip(),
            "size_bytes": size,
            "last_modified": (modified_el.text or "").strip() if modified_el is not None else "",
        })
    truncated_el = root.find(f"{_S3_NS}IsTruncated")
    truncated = (truncated_el.text or "").strip().lower() == "true" if truncated_el is not None else False
    token_el = root.find(f"{_S3_NS}NextContinuationToken")
    next_token = (token_el.text or "").strip() if (truncated and token_el is not None) else None
    return objects, next_token


def list_objects(
    account_id: str,
    bucket: str,
    prefix: str,
    access_key: str,
    secret_key: str,
    max_pages: int = 20,
) -> List[Dict[str, Any]]:
    """List every object under ``prefix``, following continuation tokens.

    Bounded by ``max_pages`` so a bucket shared with something that writes a
    great many objects cannot turn a retention sweep into an unbounded loop.
    """
    found: List[Dict[str, Any]] = []
    token: Optional[str] = None
    for _page in range(max(1, int(max_pages))):
        query = {"list-type": "2", "max-keys": "1000"}
        if prefix:
            query["prefix"] = str(prefix)
        if token:
            query["continuation-token"] = token
        _status, _headers, body = signed_request(
            "GET", account_id, bucket, "", access_key, secret_key,
            query=query, timeout=R2_METADATA_TIMEOUT_S,
        )
        objects, token = parse_list_objects(body)
        found.extend(objects)
        if not token:
            break
    return found
