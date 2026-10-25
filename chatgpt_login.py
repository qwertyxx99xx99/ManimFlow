import base64
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ISSUER = "https://auth.openai.com"
AUTH_ENDPOINT = f"{ISSUER}/api/accounts/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
RESPONSES_ENDPOINT = f"{RESOURCE}/responses"
MODELS_ENDPOINT = f"{RESOURCE}/models"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK = "http://127.0.0.1:1455/auth/callback"
REFRESH_MARGIN = 120
CALLBACK_PATTERN = re.compile(r"https?://(?:127\.0\.0\.1|localhost):1455/auth/callback\?[^\s<>\"']+")


def random_token(size):
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode()


def jwt_claims(token):
    try:
        encoded = str(token).split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        return json.loads(base64.urlsafe_b64decode(encoded))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def start():
    verifier = random_token(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = random_token(32)
    nonce = random_token(32)
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": DYNAMIC_CLIENT_ID,
        "redirect_uri": CALLBACK,
        "scope": SCOPES,
        "resource": RESOURCE,
        "nonce": nonce,
        "agent_name_hint": "ManimFlow",
        "ext_agent_host_id": f"urn:uuid:{uuid.uuid4()}",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return {"url": f"{AUTH_ENDPOINT}?{query}", "verifier": verifier, "state": state}


def callback_from_text(text):
    match = CALLBACK_PATTERN.search(str(text))
    if not match:
        return ""
    params = urllib.parse.parse_qs(urllib.parse.urlparse(match.group(0)).query)
    if params.get("error") or (params.get("code") and params.get("state")):
        return match.group(0)
    return ""


def post_form(fields):
    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OpenAI token endpoint {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc


def list_models(access_token):
    request = urllib.request.Request(
        MODELS_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Model discovery failed: HTTP {exc.code}") from exc
    items = data.get("models") if isinstance(data.get("models"), list) else data.get("data", [])
    slugs = [
        str(item.get("slug") or item.get("id") or "").strip()
        for item in items
        if not item.get("visibility") or item.get("visibility") == "list"
    ]
    return [slug for slug in slugs if slug]


def bundle_from_tokens(tokens, client_id, models=None):
    access = tokens["access_token"]
    return {
        "access_token": access,
        "refresh_token": tokens.get("refresh_token", ""),
        "client_id": client_id,
        "expires_at": float(jwt_claims(access).get("exp") or time.time() + 3000),
        "models": models or [],
        "default_model": (models or [""])[0],
    }


def complete(pending, pasted):
    callback = callback_from_text(pasted)
    if not callback:
        raise RuntimeError("Paste the full 127.0.0.1:1455/auth/callback URL from the address bar.")
    params = {key: values[0] for key, values in urllib.parse.parse_qs(urllib.parse.urlparse(callback).query).items()}
    if params.get("error"):
        raise RuntimeError(f"Sign-in failed: {params.get('error_description') or params['error']}")
    if params.get("state") != pending["state"]:
        raise RuntimeError("This callback URL belongs to a different sign-in attempt. Start again.")
    client_id = params.get("client_id", "")
    if not client_id or client_id == DYNAMIC_CLIENT_ID:
        raise RuntimeError("OpenAI did not return an issued client ID in the callback URL.")
    tokens = post_form({
        "grant_type": "authorization_code",
        "code": params["code"],
        "redirect_uri": CALLBACK,
        "client_id": client_id,
        "code_verifier": pending["verifier"],
        "resource": RESOURCE,
    })
    if not tokens.get("access_token"):
        raise RuntimeError("OpenAI did not return an access token.")
    if "chatgpt.tokens.use.direct" not in str(tokens.get("scope", "")).split():
        raise RuntimeError("ChatGPT plan usage was not granted for this sign-in.")
    models = list_models(tokens["access_token"])
    if not models:
        raise RuntimeError("No ChatGPT plan models are available for this account.")
    bundle = bundle_from_tokens(tokens, client_id, models)
    identity = jwt_claims(tokens.get("id_token", ""))
    bundle["email"] = str(identity.get("email", ""))
    return bundle


def ensure_fresh(bundle):
    if bundle["expires_at"] - time.time() > REFRESH_MARGIN:
        return bundle
    if not bundle.get("refresh_token"):
        raise RuntimeError("The ChatGPT session expired. Sign in again.")
    tokens = post_form({
        "grant_type": "refresh_token",
        "client_id": bundle["client_id"],
        "refresh_token": bundle["refresh_token"],
        "resource": RESOURCE,
    })
    if not tokens.get("access_token"):
        raise RuntimeError("OpenAI did not return an access token on refresh.")
    refreshed = bundle_from_tokens(tokens, bundle["client_id"], bundle["models"])
    refreshed["refresh_token"] = tokens.get("refresh_token") or bundle["refresh_token"]
    refreshed["email"] = bundle.get("email", "")
    return refreshed


def responses_chat(bundle, messages):
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    inputs = [
        {
            "type": "message",
            "role": m["role"],
            "content": [{"type": "output_text" if m["role"] == "assistant" else "input_text", "text": m["content"]}],
        }
        for m in messages
        if m["role"] in {"user", "assistant"}
    ]
    payload = json.dumps({
        "model": bundle["default_model"],
        "instructions": system or "You are a helpful assistant.",
        "input": inputs,
        "store": False,
        "stream": True,
        "text": {"format": {"type": "text"}},
    }).encode()
    for attempt in range(4):
        request = urllib.request.Request(
            RESPONSES_ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {bundle['access_token']}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8", errors="replace")
            text = ""
            for line in raw.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "response.output_text.delta":
                    text += event.get("delta", "")
            if not text.strip():
                raise RuntimeError("ChatGPT returned no text.")
            return text.strip()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"ChatGPT HTTP {exc.code}: {detail}") from exc
            time.sleep(min(10 * (2**attempt), 60))
    raise RuntimeError("ChatGPT exhausted its retry attempts.")
