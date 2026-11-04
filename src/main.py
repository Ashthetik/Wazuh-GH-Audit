import hashlib, hmac, json, os, time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from flask import Flask, request, jsonify
import asyncio
from typing import Any

WEBHOOK = os.environ.get("GITHUB_SECRET", "")
LOG_PATH = Path(os.environ.get("WEBHOOK_LOG_PATH", "/var/log/github-webhooks/events.log"))
MAX_BODY_BYTES = 26_000_000 # GitHub truncates to 25MB, so we'll give 1MB leeway

app = Flask(__name__)

# In-Memory De-Duplication caching for thread safety
_seen_lock = Lock()
_seen: OrderedDict[str, float] = OrderedDict()
_SEEN_MAX = 5000

def _dedup(delivery_id: str) -> bool:
    """
    Returns True if the delivery_id has already been seen
    """
    with _seen_lock:
        if delivery_id in _seen:
            return True
        _seen[delivery_id] = time.time()
        if len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False) # remove the oldest

        return False
    
def verify_signature(body: bytes, header: str, secret: str) -> bool:
    """
    Verify that the received signature header from GitHub matches our internal signature.
    This ensures that we only process legitimate, controlled, webhooks to prevent log pollution.
    """

    if not header or not header.startswith("sha256="):
        return False
    
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)

async def append_event(event_type: str, delivery_id: str, payload: dict[str, Any]): 
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    repo: dict[str, Any] = payload.get("repository", {}) or {}
    org: dict[str, Any] = payload.get("organization", {}) or {}
    sender: dict[str, Any] = payload.get("sender", {}) or {}

    # Flattened envelope for easier field extraction, original payload is attached as "raw"
    record: dict[str, Any] = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "delivery_id": delivery_id,
        "event_type": event_type,
        # "action" itself is reserved, so we have to specify our own type
        "gh_action": payload.get("action"), # eg. removed/added/created/etc.
        "org": org.get("login"),
        "repo": repo.get("full_name"),
        "repo_visibility": repo.get("visibility"),
        "actor": sender.get("login"),
        "actor_id": sender.get("id"),
        "raw": payload,
    }

    with open(LOG_PATH, "a+") as f:
        f.write(json.dumps(record, separators=(',', ':')) + '\n')

@app.post("/webhooks/github")
async def github_webhook():
    raw_body = request.get_data()
    if len(raw_body) > MAX_BODY_BYTES:
        return jsonify(error="payload too large"), 413
    
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not WEBHOOK:
        # Return a vague error, we don't need people to know what went wrong
        return jsonify(error="Internal Server Error"), 500 
    if not verify_signature(raw_body, signature, WEBHOOK):
        return jsonify(error="Signature mismatch. Rejecting."), 401
    
    delivery_id: str= request.headers.get("X-GitHub-Delivery", "")
    event_type = request.headers.get("X-GitHub-Event", "unknown")

    if delivery_id and _dedup(delivery_id):
        return jsonify(status="Duplicate, ignoring..."), 200
    
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify(error="Invalid JSON body"), 400
    
    await append_event(event_type, delivery_id, payload)

    return jsonify(status="accepted"), 200

async def main():
    app.run(host="127.0.0.1", port=8787)

if __name__ == "__main__":
    if not WEBHOOK:
        raise SystemExit("GITHUB_SECRET must be set before starting the receiver.")
    asyncio.run(main())
