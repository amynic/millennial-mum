"""SWA API proxy (v2 model, streaming) — forwards chat to the Foundry hosted agent.

Runs on a Flex Consumption plan so it can stream the hosted agent's reply
token-by-token back to the PWA (the legacy Consumption plan can't stream).

Flow: the PWA sends the running transcript -> this proxy injects the Foundry
service-principal token (kept server-side) and opens a streaming Responses call
-> we relay each ``response.output_text.delta`` chunk straight to the browser as
plain-text so the reply appears as it's generated.
"""

import json
import logging
import os
import time
import urllib.parse
import urllib.request

import azure.functions as func
import httpx
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

app = func.FunctionApp()

FOUNDRY_AGENT_ENDPOINT = os.environ.get("FOUNDRY_AGENT_ENDPOINT")
API_VERSION = "v1"
MAX_TURNS = 24

# Allow the PWA origin (browser sends CORS preflight for POST + custom headers).
ALLOWED_ORIGIN = os.environ.get(
    "ALLOWED_ORIGIN", "https://thankful-desert-05e2c3e0f.6.azurestaticapps.net"
)
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "3600",
}

_cached_token = None
_token_expiry = 0.0


def get_access_token() -> str:
    """Get an Entra token via OAuth2 client credentials (cached until expiry)."""
    global _cached_token, _token_expiry

    if _cached_token and time.time() < _token_expiry - 60:
        return _cached_token

    tenant = os.environ["AZURE_TENANT_ID"]
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": os.environ["AZURE_CLIENT_ID"],
            "client_secret": os.environ["AZURE_CLIENT_SECRET"],
            "scope": "https://ai.azure.com/.default",
        }
    ).encode("utf-8")

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


def _build_agent_input(body: dict):
    """Turn the request body into the Responses ``input`` value.

    Accepts a full transcript ({"messages": [{role, content}, ...]}, preferred)
    or a single {"message": "..."} (legacy). Prior assistant turns are sent as
    ``output_text`` and user/system turns as ``input_text`` so the hosted agent
    can rebuild the conversation for multi-turn context.
    """
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        agent_input = []
        for m in messages[-MAX_TURNS:]:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "user")
            text = (m.get("content") or "").strip()
            if not text:
                continue
            content_type = "output_text" if role == "assistant" else "input_text"
            agent_input.append(
                {
                    "type": "message",
                    "role": role,
                    "content": [{"type": content_type, "text": text}],
                }
            )
        return agent_input or None

    user_message = (body.get("message") or "").strip()
    return user_message or None


async def _relay_stream(agent_input, token):
    """Yield text chunks from the hosted agent's SSE stream as they arrive."""
    payload = {"input": agent_input, "stream": True}
    url = f"{FOUNDRY_AGENT_ENDPOINT}?api-version={API_VERSION}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Foundry-Features": "CodeAgents=V1Preview,HostedAgents=V1Preview",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(230.0, connect=30.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", "replace")
                    logging.error("Foundry stream failed (%s): %s", resp.status_code, err)
                    yield "⚠️ Sorry, something went wrong. Please try again."
                    return

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "response.output_text.delta":
                        delta = event.get("delta") or ""
                        if delta:
                            yield delta
    except Exception as e:  # pragma: no cover - depends on live Foundry
        logging.error("Foundry stream error: %s", e)
        yield "⚠️ Sorry, something went wrong. Please try again."


@app.route(route="chat", methods=[func.HttpMethod.POST, func.HttpMethod.OPTIONS],
           auth_level=func.AuthLevel.ANONYMOUS)
async def chat(req: Request):
    if req.method == "OPTIONS":
        return Response(status_code=204, headers=_CORS_HEADERS)

    try:
        body = await req.json()
    except Exception:
        return Response(
            content=json.dumps({"reply": "Invalid request"}),
            status_code=400,
            media_type="application/json",
            headers=_CORS_HEADERS,
        )

    agent_input = _build_agent_input(body)
    if agent_input is None:
        return Response(
            content=json.dumps({"reply": "Missing message content"}),
            status_code=400,
            media_type="application/json",
            headers=_CORS_HEADERS,
        )

    try:
        token = get_access_token()
    except Exception as e:
        logging.error("Auth failed: %s", e)
        return Response(
            content=json.dumps({"reply": "⚠️ Authentication unavailable. Please try again later."}),
            status_code=500,
            media_type="application/json",
            headers=_CORS_HEADERS,
        )

    stream_headers = {
        **_CORS_HEADERS,
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        _relay_stream(agent_input, token),
        media_type="text/plain; charset=utf-8",
        headers=stream_headers,
    )
