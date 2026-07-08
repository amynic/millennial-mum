"""SWA API proxy — forwards chat requests to the Foundry hosted agent."""

import json
import logging
import os
import urllib.request
import urllib.parse

import azure.functions as func

FOUNDRY_AGENT_ENDPOINT = os.environ.get(
    "FOUNDRY_AGENT_ENDPOINT",
    "https://ai-account-yd7znjiks3cma.services.ai.azure.com/api/projects/ai-project-millennial-mum-agent/agents/millennial-mum/endpoint/protocols/openai/responses",
)
API_VERSION = "2025-11-15-preview"

_cached_token = None
_token_expiry = 0


def get_access_token():
    """Get Entra token via OAuth2 client credentials (no SDK needed)."""
    global _cached_token, _token_expiry
    import time

    if _cached_token and time.time() < _token_expiry - 60:
        return _cached_token

    tenant = os.environ["AZURE_TENANT_ID"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["AZURE_CLIENT_ID"],
        "client_secret": os.environ["AZURE_CLIENT_SECRET"],
        "scope": "https://ai.azure.com/.default",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    _cached_token = result["access_token"]
    _token_expiry = time.time() + result.get("expires_in", 3600)
    return _cached_token


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"reply": "Invalid request"}),
            status_code=400,
            mimetype="application/json",
        )

    user_message = body.get("message", "")
    if not user_message:
        return func.HttpResponse(
            json.dumps({"reply": "Missing 'message' field"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        token = get_access_token()
    except Exception as e:
        logging.error(f"Auth failed: {e}")
        return func.HttpResponse(
            json.dumps({"reply": "⚠️ Authentication unavailable. Please try again later."}),
            status_code=500,
            mimetype="application/json",
        )

    payload = json.dumps({"input": user_message}).encode("utf-8")
    url = f"{FOUNDRY_AGENT_ENDPOINT}?api-version={API_VERSION}"

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Foundry-Features": "CodeAgents=V1Preview,HostedAgents=V1Preview",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.readable() else str(e)
        logging.error(f"Foundry call failed ({e.code}): {error_body}")
        return func.HttpResponse(
            json.dumps({"reply": "⚠️ Sorry, something went wrong. Please try again."}),
            status_code=502,
            mimetype="application/json",
        )
    except Exception as e:
        logging.error(f"Foundry call failed: {e}")
        return func.HttpResponse(
            json.dumps({"reply": "⚠️ Sorry, something went wrong. Please try again."}),
            status_code=502,
            mimetype="application/json",
        )

    # Extract the assistant reply
    reply = "Sorry, I couldn't generate a response."
    for item in data.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    reply = block.get("text", reply)
                    break
            break

    return func.HttpResponse(
        json.dumps({"reply": reply}),
        mimetype="application/json",
    )
