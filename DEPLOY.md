# Go-live runbook — Millennial Mum

Two live deploys are fully scaffolded and gated only on you (real Azure
resources + auth). Run them in order; step 3 needs the endpoint from step 2.

Foundry project (existing — reused, no new model spend):
- Endpoint: `https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum`
- RG `rg-millennial-mum` · sub `7a880728-70d3-49d0-adde-4250716cfd94` (ai-team)
- 6 role-named deployments: triage→gpt-5-mini, planner→gpt-5-mini,
  kitchen→gpt-5-nano, memory→gpt-5-nano, health→gpt-5, admin-budget→DeepSeek-V3.2

---

## 1. Evals in Foundry — DONE ✅
Portal-tracked evaluations are already registered (Foundry → project → Evaluations):
`mm-decomposed-v2-pretuning` (routing 0.86) and `mm-decomposed-baseline-tuned`
(routing 0.96, safety 1.0). Re-run any time:
```
$env:FOUNDRY_PROJECT_ENDPOINT = "https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum"
python -m evals.foundry_eval --run evals/results/decomposed.json --name <name>
```

---

## 2. Host the multi-agent in Foundry (azd Hosted Agent)
**Cost:** runs a container continuously (~1 vCPU / 2 GiB) — ongoing compute.
```
azd auth login
azd env new millennial-mum
azd env set AZURE_AI_PROJECT "/subscriptions/7a880728-70d3-49d0-adde-4250716cfd94/resourceGroups/rg-millennial-mum/providers/Microsoft.CognitiveServices/accounts/millennial-mum-foundry/projects/millennial-mum"
azd env set FOUNDRY_PROJECT_ENDPOINT "https://millennial-mum-foundry.services.ai.azure.com/api/projects/millennial-mum"
azd env set APPLICATIONINSIGHTS_CONNECTION_STRING "<millennial-mum-insights conn string>"
azd extension upgrade azure.ai.agents   # needs >= 1.0.0-beta.9
azd up
```
Capture the output **`AGENT_MILLENNIAL_MUM_RESPONSES_ENDPOINT`** — that's the
hosted `/responses` endpoint the phone app calls.

---

## 3. Phone app — Azure Static Web App (Free, $0)
Needs the endpoint from step 2 + a service principal for the proxy's
client-credentials auth to the agent.
```
az staticwebapp create -n millennial-mum-web -g rg-millennial-mum -l eastus2 --sku Free
# repo secret used by .github/workflows/azure-static-web-apps.yml:
az staticwebapp secrets list -n millennial-mum-web   # -> AZURE_STATIC_WEB_APPS_API_TOKEN (set as a GitHub Actions secret)
# app settings consumed by the api/chat Functions proxy:
az staticwebapp appsettings set -n millennial-mum-web --setting-names `
  FOUNDRY_AGENT_ENDPOINT="<AGENT_MILLENNIAL_MUM_RESPONSES_ENDPOINT>" `
  AZURE_TENANT_ID="46946eec-4e27-4270-876d-953a3b711bf8" `
  AZURE_CLIENT_ID="<sp app id>" AZURE_CLIENT_SECRET="<sp secret>"
```
Then push to `main` to trigger the workflow. Post-deploy: update the CORS
placeholder hostname in `server_af.py` to the real SWA hostname.

**On iPhone:** open the SWA URL in Safari → Share → **Add to Home Screen**.
Installable app icon, offline shell, no secrets on device (proxy holds auth).
