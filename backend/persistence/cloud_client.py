"""B6 — the HTTP transport: one door out of the site, and it is signed.

Two transports, on purpose
--------------------------
* **This one (HTTP)** carries *discrete events* — one alert, one upload manifest,
  one drill record. They are small, infrequent, and each wants an idempotency key the
  receiver can dedupe on, which is exactly what a request body carrying `batch_id`
  gives you.
* **`sync.py`'s Postgres path** carries *bulk rows* (hourly rollups, alert history)
  through a `synced_at` watermark, because a hundred thousand rows through HTTP are
  a worse deal than one `execute_values` upsert.

Both exist because the two jobs have different shapes; neither is a fallback for the
other, and a phase report that claimed "the cloud is synced" without saying *which
path* would be hiding the distinction.

Why the signature is an HMAC and not a shared-secret header
----------------------------------------------------------
A `X-Signature: <key>` header proves the sender *knows a string*, which is also what
an attacker who reads one request knows. HMAC-SHA256 over the **exact bytes sent**
proves the body was not altered in flight and cannot be replayed with a different
payload — so a batch that is corrupted by a flaky link is rejected instead of being
written into the central record as truth. The digest is computed on the *compressed*
bytes: that is what goes on the wire, so that is what the receiver can verify.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
import uuid

import config

#: The result of one push. A dataclass would be tidier; a dict is what the caller
#: (sync.py, and the tests) actually wants to merge into a stats block.
def _result(ok: bool, *, status: int | None = None, error: str | None = None,
            duplicate: bool = False, bytes_raw: int = 0, bytes_gzip: int = 0) -> dict:
    return {"ok": bool(ok), "http_status": status, "error": error,
            "duplicate": bool(duplicate), "bytes_raw": bytes_raw,
            "bytes_gzip": bytes_gzip}


def sign_payload(body: bytes, key: str | None = None) -> str:
    """HMAC-SHA256 over the bytes about to be sent. Empty key ⇒ empty signature.

    Empty is not "no security" by accident: with no key configured the endpoint is
    a bench mock on localhost, and the header is omitted entirely rather than sent
    as a lie that a real deployment might trust.
    """
    key = config.CLOUD_HMAC_KEY if key is None else key
    if not key:
        return ""
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def endpoint(base: str | None = None) -> str:
    base = (config.CLOUD_HTTP_URL if base is None else base) or ""
    return base.rstrip("/") + "/ingest" if base else ""


def configured() -> bool:
    return bool(config.CLOUD_HTTP_URL)


def _encode(payload) -> tuple[bytes, bytes, str]:
    """(raw, gzipped, batch_id). The gzip is the wire form; the raw is for the log."""
    batch_id = str((payload or {}).get("batch_id") or uuid.uuid4().hex[:12])
    body = dict(payload or {})
    body["batch_id"] = batch_id
    body["site_id"] = body.get("site_id") or config.SITE_ID
    body["sent_at"] = body.get("sent_at") or time.time()
    raw = json.dumps(body, separators=(",", ":"), default=str).encode()
    return raw, gzip.compress(raw), batch_id


def post_batch(payload, *, url: str | None = None, timeout: float | None = None,
               key: str | None = None) -> dict:
    """POST one gzip+HMAC batch. **Never raises** — returns a result dict.

    Never raising is not politeness: this is called from inside the sync daemon, and
    an exception escaping here would kill the thread that is the site's only way to
    ever talk to the cloud again. Every failure mode the network has (DNS, TLS, a
    cold-starting free tier that takes 30 s to answer, a 502 from a sleeping
    database) arrives as `ok: False` plus a reason the outbox can store.
    """
    target = url or endpoint()
    if not target:
        return _result(False, error="cloud HTTP endpoint not configured")
    raw, gz, batch_id = _encode(payload)
    headers = {
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        "X-Batch-Id": batch_id,
        "X-Site-Id": str(config.SITE_ID),
    }
    sig = sign_payload(gz, key)
    if sig:
        headers["X-NS-Signature"] = sig
    req = urllib.request.Request(target, data=gz, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or config.CLOUD_TIMEOUT_S)) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = {}
            try:
                parsed = json.loads(body) if body else {}
            except ValueError:
                parsed = {}
            return _result(200 <= resp.status < 300, status=resp.status,
                           duplicate=bool(parsed.get("duplicate")),
                           bytes_raw=len(raw), bytes_gzip=len(gz))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:                                   # noqa: BLE001
            pass
        return _result(False, status=exc.code,
                       error=f"HTTP {exc.code}{': ' + detail if detail else ''}",
                       bytes_raw=len(raw), bytes_gzip=len(gz))
    except Exception as exc:                                # noqa: BLE001
        return _result(False, error=f"{type(exc).__name__}: {exc}"[:200],
                       bytes_raw=len(raw), bytes_gzip=len(gz))


def probe_backhaul(*, url: str | None = None, timeout: float | None = None) -> dict:
    """Is there a way out? One cheap GET, and it tells the truth about *why* not.

    Used by `/api/sync/status` and by the demo: "the link is down" and "the endpoint
    is wrong" are different problems with the same symptom, and a chip that says
    only `offline` makes the operator guess which one they have.
    """
    base = (url or config.CLOUD_HTTP_URL or "").rstrip("/")
    if not base:
        return {"ok": False, "reason": "no endpoint configured", "ms": None}
    t0 = time.time()
    try:
        with urllib.request.urlopen(base + "/health", timeout=float(timeout or config.CLOUD_TIMEOUT_S)) as resp:
            return {"ok": 200 <= resp.status < 300, "http_status": resp.status,
                    "ms": round((time.time() - t0) * 1000, 1)}
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:160],
                "ms": round((time.time() - t0) * 1000, 1)}


def retry_delay(attempts: int) -> int:
    """The backoff table, re-exported so a caller need not import two modules."""
    from persistence.outbox import backoff_s
    return backoff_s(attempts)
