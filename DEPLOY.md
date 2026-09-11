# Go-live runbook — Millennial Mum (LIVE)

Both the hosted multi-agent and the phone PWA are deployed and verified live.
This documents what's running and how to redeploy.

## Live resources (rg-millennial-mum, sub 7a880728-…, tenant 46946eec-…)

| Piece | Resource | URL |
|---|---|---|
| Foundry project | `millennial-mum-foundry` / project `millennial-mum` | https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum |
| Hosted agent | `millennial-mum` (Foundry Hosted Agent, code deploy) | `…/agents/millennial-mum/endpoint/protocols/openai/responses?api-version=v1` |
| Phone PWA | `millennial-mum-web` (Static Web App, **Free**) | https://thankful-desert-05e2c3e0f.6.azurestaticapps.net |
| API proxy | `millennial-mum-api` (Functions, Consumption, Py 3.11) | https://millennial-mum-api.azurewebsites.net/api/chat |
| Proxy auth | SP `millennial-mum-web-proxy` (appId 7fd3c7c4-…), role **Azure AI User** on the Foundry account | client-credentials |

**On iPhone:** open the PWA URL in Safari → Share → **Add to Home Screen**.

## Architecture (why the proxy is its own Function App)

Phone PWA → (CORS) → `millennial-mum-api` Function App → client-credentials SP
token → Foundry hosted agent → decomposed orchestrator (triage → specialist →
compose) → reply. Auth stays server-side; no secrets on the device.

Warm multi-agent replies run ~28–80s. **SWA managed functions cap responses at
45s**, so the proxy runs on its own Consumption Function App (HTTP up to 230s)
and the SWA stays on the Free tier. The browser calls the Function App directly
(CORS allow-list = the SWA origin), rather than via an SWA linked backend (which
would need the $9/mo Standard tier).

## Redeploy — hosted agent

```
azd deploy millennial-mum          # rebuilds server_af.py, new agent version
azd ai agent invoke millennial-mum '{"input":"..."}'   # smoke test
```
Notes: `requirements.txt` MUST list the decomposed deps (agent-framework,
azure-ai-agentserver-responses, azure-identity, …) — that's what the remote
build installs. Responses protocol version is `2.0.0`. Env (FOUNDRY_PROJECT_
ENDPOINT, APPLICATIONINSIGHTS_CONNECTION_STRING) is set via `azd env set`.

## Redeploy — API proxy (Function App)

```
Compress-Archive -Path api\host.json,api\requirements.txt,api\chat -DestinationPath api.zip -Force
az functionapp deployment source config-zip -n millennial-mum-api -g rg-millennial-mum --src api.zip --build-remote true
```
App settings on `millennial-mum-api`: FOUNDRY_AGENT_ENDPOINT (no query string),
AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET. CORS allow-list includes
the SWA origin + http://localhost:4280.

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

## Follow-ups / hardening

- The Function App endpoint is anonymous (as the SWA managed function was). Add
  auth (Entra Easy Auth on the Function App, or an SWA-issued header) if the URL
  is shared beyond the owner.
- Consider switching the proxy from an SP secret to the Function App's
  system-assigned managed identity (grant it Azure AI User) to drop the secret.
- Streaming: first byte is ~1s; a streaming proxy + frontend would make the
  50–80s replies feel instant.
- Tool-recall tuning (0.863 → 0.800) is the next eval target.
