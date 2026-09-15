"""SWA API proxy (v2 model, streaming) — forwards chat to the Foundry hosted agent.

Runs on a Flex Consumption plan so it can stream the hosted agent's reply
token-by-token back to the PWA (the legacy Consumption plan can't stream).

Flow: the PWA sends the running transcript -> this proxy injects the Foundry
service-principal token (kept server-side) and opens a streaming Responses call
-> we relay each ``response.output_text.delta`` chunk straight to the browser as
plain-text so the reply appears as it's generated.

Latency instrumentation
-----------------------
This tier owns three hops that were previously invisible: token acquisition,
connection setup to Foundry, and the wait for the hosted agent's first delta.
Each is timed and logged as a single content-free JSON line tagged with the
turn's correlation id, which is also forwarded to the hosted agent (as
``x-client-mm-request-id``) and echoed to the browser, so one turn can be joined
across all three tiers.

The timing helpers are duplicated here rather than imported from
``agents.latency``: this Function App deploys on its own with only the three
packages in ``requirements.txt``, and must not depend on the agents package.
"""

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import uuid

import azure.functions as func
import httpx
from azurefunctions.extensions.http.fastapi import Request, Response, StreamingResponse

app = func.FunctionApp()

FOUNDRY_AGENT_ENDPOINT = os.environ.get("FOUNDRY_AGENT_ENDPOINT")
API_VERSION = "v1"
MAX_TURNS = 24

# Correlation id plumbing. The browser sends the id in the JSON body so it
# doesn't have to add a custom request header (and so an older deployment of
# this proxy can never reject the PWA at CORS preflight); we also accept it as a
# header for curl/benchmark callers. The ``x-client-`` prefix on the outbound
# header is what makes the Foundry agent server surface it to the hosted agent.
REQUEST_ID_HEADER = "x-client-mm-request-id"
REQUEST_ID_FIELD = "request_id"

# Allow the PWA origin (browser sends CORS preflight for POST + custom headers).
ALLOWED_ORIGIN = os.environ.get(
    "ALLOWED_ORIGIN", "https://thankful-desert-05e2c3e0f.6.azurestaticapps.net"
)
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": f"Content-Type, {REQUEST_ID_HEADER}",
    "Access-Control-Expose-Headers": REQUEST_ID_HEADER,
    "Access-Control-Max-Age": "3600",
}

_cached_token = None
_token_expiry = 0.0

# Cold/warm classification: the first turn a worker serves pays container start,
# client construction, and a token fetch. Mixing those into warm percentiles
# hides the real steady-state latency.
_requests_served = 0


def _resolve_request_id(req, body: dict) -> str:
    """Pick up the caller's correlation id, or mint one for this turn."""
    raw = ""
    try:
        raw = req.headers.get(REQUEST_ID_HEADER) or ""
    except Exception:
        raw = ""
    if not raw and isinstance(body, dict):
        raw = body.get(REQUEST_ID_FIELD) or ""
    cleaned = str(raw).strip()[:64]
    return cleaned or uuid.uuid4().hex


def _claim_cold_start() -> bool:
    """``True`` for the first turn served by this worker process."""
    global _requests_served
    _requests_served += 1
    return _requests_served == 1


def _log_latency(record: dict) -> None:
    """Emit one content-free JSON latency line (queryable in App Insights)."""
    logging.info("mm.latency %s", json.dumps(record, sort_keys=True))


def token_is_cached() -> bool:
    """Whether the next :func:`get_access_token` will be served from cache.

    Read *before* fetching so the latency record can say whether the auth hop
    was a free cache hit or a real round trip to Entra — a distinction that
    otherwise looks like unexplained variance in the proxy's timings.
    """
    return bool(_cached_token) and time.time() < _token_expiry - 60


def get_access_token() -> str:
    """Get an Entra token via OAuth2 client credentials (cached until expiry)."""
    global _cached_token, _token_expiry

    if token_is_cached():
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
    or a single {"message": "..."} (legacy). Message content is sent as a
    string so the hosted Responses server expands every turn to valid
    ``input_text`` content. ``output_text`` is an output-only shape that also
    requires annotations and logprobs; using it for assistant history caused
    the transcript to fail request validation.
    """
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        agent_input = []
        for m in messages[-MAX_TURNS:]:
            if not isinstance(m, dict):
                continue
            role = "assistant" if m.get("role") == "assistant" else "user"
            text = (m.get("content") or "").strip()
            if not text:
                continue
            agent_input.append(
                {
                    "type": "message",
                    "role": role,
                    "content": text,
                }
            )
        return agent_input or None

    user_message = (body.get("message") or "").strip()
    return user_message or None


async def _relay_stream(agent_input, token, request_id, record):
    """Yield text chunks from the hosted agent's SSE stream as they arrive.

    Also records the hops this tier owns — upstream response headers, first
    delta, and total — onto ``record``, which is emitted as one content-free
    latency line when the stream ends (including on failure, so slow failures
    are measured too).
    """
    payload = {"input": agent_input, "stream": True}
    url = f"{FOUNDRY_AGENT_ENDPOINT}?api-version={API_VERSION}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Foundry-Features": "CodeAgents=V1Preview,HostedAgents=V1Preview",
        # Forwarded so the hosted agent stamps the same id on its own spans.
        REQUEST_ID_HEADER: request_id,
        "x-request-id": request_id,
    }

    started = time.perf_counter()

    def elapsed_ms() -> float:
        return (time.perf_counter() - started) * 1000

    chunks = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(230.0, connect=30.0)) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                record["upstream_headers_ms"] = round(elapsed_ms(), 1)
                record["status"] = resp.status_code
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", "replace")
                    logging.error(
                        "Foundry stream failed [%s] (%s): %s", request_id, resp.status_code, err
                    )
                    record["failed"] = True
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
                            if chunks == 0:
                                record["first_token_ms"] = round(elapsed_ms(), 1)
                            chunks += 1
                            yield delta
    except Exception as e:  # pragma: no cover - depends on live Foundry
        logging.error("Foundry stream error [%s]: %s", request_id, e)
        record["failed"] = True
        yield "⚠️ Sorry, something went wrong. Please try again."
    finally:
        record["chunks"] = chunks
        record["total_ms"] = round(elapsed_ms(), 1)
        _log_latency(record)


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

    request_id = _resolve_request_id(req, body)
    cold = _claim_cold_start()
    error_headers = {**_CORS_HEADERS, REQUEST_ID_HEADER: request_id}

    agent_input = _build_agent_input(body)
    if agent_input is None:
        return Response(
            content=json.dumps({"reply": "Missing message content"}),
            status_code=400,
            media_type="application/json",
            headers=error_headers,
        )

    auth_started = time.perf_counter()
    auth_cached = token_is_cached()
    try:
        token = get_access_token()
    except Exception as e:
        logging.error("Auth failed [%s]: %s", request_id, e)
        _log_latency(
            {
                "request_id": request_id,
                "component": "proxy",
                "cold": cold,
                "auth_ms": round((time.perf_counter() - auth_started) * 1000, 1),
                "auth_cached": auth_cached,
                "failed": True,
                "stage": "auth",
            }
        )
        return Response(
            content=json.dumps({"reply": "⚠️ Authentication unavailable. Please try again later."}),
            status_code=500,
            media_type="application/json",
            headers=error_headers,
        )
    auth_ms = (time.perf_counter() - auth_started) * 1000

    latency_record = {
        "request_id": request_id,
        "component": "proxy",
        "cold": cold,
        "turns": len(agent_input) if isinstance(agent_input, list) else 1,
        "auth_ms": round(auth_ms, 1),
        "auth_cached": auth_cached,
    }

    stream_headers = {
        **_CORS_HEADERS,
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        # Echoed so the browser (and the benchmark harness) can log the same id.
        REQUEST_ID_HEADER: request_id,
    }
    return StreamingResponse(
        _relay_stream(agent_input, token, request_id, latency_record),
        media_type="text/plain; charset=utf-8",
        headers=stream_headers,
    )
