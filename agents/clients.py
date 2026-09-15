"""Foundry chat-client factory (per-domain model, multi-provider).

Each specialist gets its own client bound to its assigned Foundry deployment.
OpenAI/Azure-sold models use ``FoundryChatClient``; Claude models use
``AnthropicFoundryClient`` (Anthropic served through Foundry). Credentials
come from Azure identity (``DefaultAzureCredential``) — no GITHUB_TOKEN.

Client construction is lazy and import-guarded so this module loads in
environments without Foundry credentials or optional provider plugins;
the heavy imports only happen when a client is actually built.
"""

from __future__ import annotations

import os

from agents.config import AGENT_MODELS, ModelSpec


def _project_endpoint() -> str:
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT is not set. Provision a Foundry project and set "
            "the endpoint (and per-agent model env vars) before building live agents."
        )
    return endpoint


def _credential():
    # AzureCliCredential works locally after `az login`; DefaultAzureCredential
    # also picks up managed identity when hosted.
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def make_client(spec: ModelSpec):
    """Build the chat client for a model spec, choosing the provider."""
    endpoint = _project_endpoint()
    credential = _credential()
    deployment = spec.deployment()

    if spec.provider == "anthropic_foundry":
        from agent_framework.foundry import AnthropicFoundryClient

        return AnthropicFoundryClient(
            project_endpoint=endpoint, model=deployment, credential=credential
        )

    from agent_framework.foundry import FoundryChatClient

    return FoundryChatClient(
        project_endpoint=endpoint, model=deployment, credential=credential
    )


def client_for(domain: str):
    return make_client(AGENT_MODELS[domain])


def options_for(domain: str) -> dict:
    """Default Agent options for a domain (currently the reasoning budget).

    Kept beside ``client_for`` so every place that builds an agent picks up the
    same settings; a specialist built without these silently reverts to the
    model default, which is where almost all of the time-to-first-token went.
    """
    return AGENT_MODELS[domain].default_options()
