# Go-live runbook — Millennial Mum (LIVE)

Both the hosted multi-agent and the phone PWA are deployed and verified live.
This documents what's running and how to redeploy.

## Live resources (rg-millennial-mum, sub 7a880728-…, tenant 46946eec-…)

| Piece | Resource | URL |
|---|---|---|
| Foundry project | `millennial-mum-foundry` / project `millennial-mum` | https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum |
| Hosted agent | `millennial-mum` (Foundry Hosted Agent, code deploy) | `…/agents/millennial-mum/endpoint/protocols/openai/responses?api-version=v1` |
| Phone PWA | `millennial-mum-web` (Static Web App, **Free**) | https://thankful-desert-05e2c3e0f.6.azurestaticapps.net |
| API proxy | `millennial-mum-api-flex` (Functions, **Flex Consumption**, Py 3.11, v2 model, **streaming**) | https://millennial-mum-api-flex.azurewebsites.net/api/chat |
| Proxy auth | SP `millennial-mum-web-proxy` (appId 7fd3c7c4-…), role **Azure AI User** on the Foundry account | client-credentials |

**On iPhone:** open the PWA URL in Safari → Share → **Add to Home Screen**.

## Architecture (why the proxy is its own Function App)

Phone PWA → (CORS) → `millennial-mum-api-flex` Function App → client-credentials
SP token → Foundry hosted agent → decomposed orchestrator (triage → specialist →
compose) → reply. Auth stays server-side; no secrets on the device.

The reply **streams** end-to-end: the hosted agent yields text deltas
(`agent.run(stream=True)` → `TextResponse` SSE), the proxy relays each
`response.output_text.delta` as `text/plain` chunks, and the PWA appends them to
the bubble as they arrive. Real HTTP streaming needs the **Flex Consumption**
plan + the v2 Python model — the legacy Consumption plan can't stream. Note:
time-to-first-token is still ~50–70s because the router calls the specialist
(blocking) before composing; streaming makes the composed reply render
progressively rather than after the full 28–80s wait.

Warm multi-agent replies run ~28–80s. **SWA managed functions cap responses at
45s**, so the proxy runs on its own Function App (HTTP up to 230s) and the SWA
stays on the Free tier. The browser calls the Function App directly (CORS
allow-list = the SWA origin), rather than via an SWA linked backend (which would
need the $9/mo Standard tier).

## Redeploy — hosted agent

```
azd deploy millennial-mum          # rebuilds server_af.py, new agent version
azd ai agent invoke millennial-mum '{"input":"..."}'   # smoke test
```
Notes: `requirements.txt` MUST list the decomposed deps (agent-framework,
azure-ai-agentserver-responses, azure-identity, …) — that's what the remote
build installs. Responses protocol version is `2.0.0`. Env (FOUNDRY_PROJECT_
ENDPOINT, APPLICATIONINSIGHTS_CONNECTION_STRING) is set via `azd env set`.

## Redeploy — API proxy (Function App, Flex Consumption, streaming)

The proxy is the **v2 Python model** (`api/function_app.py`) on a **Flex
Consumption** plan. Deploy with Core Tools so Oryx does the remote build:

```
cd api
func azure functionapp publish millennial-mum-api-flex --python
```
Required app settings on `millennial-mum-api-flex`: FOUNDRY_AGENT_ENDPOINT (no
query string), AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
ALLOWED_ORIGIN (the SWA origin), **PYTHON_ENABLE_INIT_INDEXING=1** and
**AzureWebJobsFeatureFlags=EnableWorkerIndexing** (both REQUIRED for HTTP
streaming — without them the worker indexes 0 functions and every call 404s).
The Function App platform CORS list must also include the SWA origin; otherwise
Flex Consumption can intercept the OPTIONS preflight before the Python handler
adds its headers:

```
az functionapp cors add -g rg-millennial-mum -n millennial-mum-api-flex \
  --allowed-origins https://thankful-desert-05e2c3e0f.6.azurestaticapps.net
az functionapp restart -g rg-millennial-mum -n millennial-mum-api-flex
```

The handler still returns CORS headers for POST and OPTIONS responses.
Requirements: `azure-functions`, `azurefunctions-extensions-http-fastapi`,
`httpx`. The legacy `millennial-mum-api` (Consumption, non-streaming) has been
deleted — V2 runs entirely on `millennial-mum-api-flex`.

## Durable tool storage (shopping list + family profile)

The JSON-backed tools persist to **Azure Blob Storage**, not the container
filesystem. The hosted agent's disk is ephemeral and per-replica, so the old
`MM_DATA_DIR=/tmp/millennial-mum` setting meant a shopping list added in one
conversation was gone in the next. `tools/storage.py` now writes each document
as a blob and guards every update with the blob's ETag (`If-Match`), so two
concurrent turns can't clobber each other.

One-time setup (reuses the existing `mmumapi4095` account):

```
azd env set MM_BLOB_ACCOUNT_URL https://mmumapi4095.blob.core.windows.net
azd env set AZURE_AI_PROJECT_ID $(az resource show -g rg-millennial-mum \
  -n millennial-mum-foundry/millennial-mum \
  --resource-type Microsoft.CognitiveServices/accounts/projects --query id -o tsv)
azd deploy millennial-mum
```

The hosted agent does **not** run as the Foundry account or project managed
identity. It runs as a per-agent *instance identity*, and that is the principal
that needs the role. Read it off the agent definition:

```
$t = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
curl -s -H "Authorization: Bearer $t" \
  -H "Foundry-Features: CodeAgents=V1Preview,HostedAgents=V1Preview" \
  "https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum/agents/millennial-mum?api-version=v1" \
  | jq -r .instance_identity.principal_id

az role assignment create \
  --role "Storage Blob Data Contributor" \
  --assignee-object-id <instance-identity-principal-id> \
  --assignee-principal-type ServicePrincipal \
  --scope $(az storage account show -g rg-millennial-mum -n mmumapi4095 --query id -o tsv)
```

Data-plane RBAC takes a couple of minutes to propagate. If the identity lacks
the role the agent still answers — reads fail soft back to defaults — but writes
return a `HttpResponseError` and the user sees a "hiccup" message. The instance
identity is stable across redeploys (verified across versions 11→13), so the
role assignment is a one-time step.

`azd deploy` occasionally fails with `AzureDeveloperCLICredential: exit status 1`
while resolving the agent target. This is a token hand-off between azd and the
`azure.ai.agents` extension, not a problem with your login — `azd auth token`
will succeed for every scope while it happens. Retry; it clears on its own.

Verify after deploy — add an item, then force a fresh conversation and ask for
the list back:

```
azd ai agent invoke millennial-mum "add tomato puree to my shopping list"
azd ai agent invoke --new-session --new-conversation "what is on my shopping list?"
```

Local dev needs nothing: with `MM_BLOB_ACCOUNT_URL` unset the tools fall back to
a local file (`MM_DATA_DIR`, or the repo root). `MM_BLOB_PREFIX` is reserved for
per-family blob paths once the app is multi-tenant — today every user of a
deployment shares one list.

## Redeploy — PWA (Static Web App)

```
swa deploy .\frontend --deployment-token <token> --env production
# token: az staticwebapp secrets list -n millennial-mum-web --query properties.apiKey -o tsv
```
`frontend/app.js` `API_ENDPOINT` points at the Function App URL.

## Evals in Foundry — DONE

Portal-tracked (Foundry → project → Evaluations): `mm-decomposed-v2-pretuning`
(routing 0.86) and `mm-decomposed-baseline-tuned` (routing 0.96, safety 1.0).
Re-run: `python -m evals.foundry_eval --run evals/results/decomposed.json --name <name>`.

## Latency — benchmark & traces

Every turn is measured end-to-end under one `request_id` (see the README's
observability section for the KQL). To reproduce a baseline against the live
proxy:

```
# warm baseline: first request is discarded as the cold sample
python -m evals.latency_bench --repeat 3 --out evals/results/latency_baseline.json

# include the cold request in its own bucket
python -m evals.latency_bench --repeat 3 --include-cold

# one case only
python -m evals.latency_bench --case kitchen_simple --repeat 5
```

It reports p50/p95/p99 for **time to first token** and total duration, cold and
warm separated, plus a per-kind breakdown (single-domain, multi-domain, memory,
tool-calling, health, hard). Only durations and reply *lengths* are recorded —
never reply text.

To compare the two routing paths, flip the fast path on the hosted agent and
re-run the same cases:

```
azd env set MM_FAST_PATH false && azd deploy millennial-mum
python -m evals.latency_bench --repeat 3 --out evals/results/latency_orchestrated.json

azd env set MM_FAST_PATH true && azd deploy millennial-mum
python -m evals.latency_bench --repeat 3 --out evals/results/latency_fastpath.json
```

Then diff the two runs and check them against the target:

```
python -m evals.latency_compare \
    evals/results/latency_orchestrated.json \
    evals/results/latency_fastpath.json
```

`MM_FAST_PATH` and `MM_FAST_PATH_EXCLUDE` only reach the container because
they are listed in `azure.yaml`'s `env:` block — **only the variables named
there are passed to the hosted agent**, so adding a new one to the azd env
without also adding it to `azure.yaml` silently does nothing.

`MM_FAST_PATH_EXCLUDE` (default `health`) pins domains to full orchestration.
A blank value means "unset" and keeps the default, because azd substitutes an
empty string for a variable nobody set — use the literal `none` to clear the
list. Leave Health excluded until the fast path has its own safety evaluation.

Target for common single-domain turns: **warm p50 time-to-first-token under 5s,
warm p95 under 10s.**

### Which azd environment deploys the agent

The live agent version was deployed from the **`mm-prod`** azd environment,
which lives in the `amynic-automatic-system` worktree — not in the main
checkout, and not in this one. It is the only environment with
`MM_BLOB_ACCOUNT_URL` set, so deploying from any of the other envs
(`millennial-mum`, `amynic-fluffy-winner`) would drop that variable and break
the shopping list and family profile tools.

Check before deploying:

```
azd env list                       # nothing here means no .azure/ in this tree
azd env get-values | Select-String MM_BLOB_ACCOUNT_URL
```

The agent endpoint routes **100% of traffic to `@latest`**, so a deploy takes
effect immediately for the live PWA — there is no canary step. Take the
orchestrated baseline before deploying a build that has the fast path on.

## Follow-ups / hardening

- The Function App endpoint is anonymous (as the SWA managed function was). Add
  auth (Entra Easy Auth on the Function App, or an SWA-issued header) if the URL
  is shared beyond the owner.
- Consider switching the proxy from an SP secret to the Function App's
  system-assigned managed identity (grant it Azure AI User) to drop the secret.
- Streaming is live (Flex Consumption + v2 model). Time-to-first-token is now
  attacked by the direct-specialist fast path (`MM_FAST_PATH`, on by default):
  confidently single-domain turns skip the orchestrator's routing and
  composition generations. Remaining wins: run independent specialists
  concurrently for multi-domain turns, reuse the Foundry client across
  invocations, set minimum instances to remove proxy cold starts, and give
  Health a fast-path safety evaluation so it can stop being excluded.
- Tool-recall tuning (0.863 → 0.800) is the next eval target.
