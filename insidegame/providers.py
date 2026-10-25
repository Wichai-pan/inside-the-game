"""One place that decides which model backs the Agent Framework agents.

Every workflow takes an Agent Framework chat client; `get_client()` builds it from environment
variables, so moving from a laptop to Microsoft Foundry is a configuration change, not a code change.

    INSIDEGAME_PROVIDER   ollama (default) | foundry | azure_openai

    ollama        OLLAMA_MODEL (default qwen3.5:9b), OLLAMA_HOST (default http://localhost:11434)
    foundry       FOUNDRY_PROJECT_ENDPOINT  https://<resource>.services.ai.azure.com/api/projects/<project>
                  FOUNDRY_MODEL             model deployment name in the project, e.g. gpt-4.1-mini
                  auth: Microsoft Entra ID via DefaultAzureCredential (`az login`, managed identity, ...)
    azure_openai  AZURE_OPENAI_ENDPOINT     https://<resource>.openai.azure.com
                  AZURE_OPENAI_MODEL        deployment name
                  AZURE_OPENAI_API_KEY      optional; without it Entra ID (DefaultAzureCredential) is used
                  AZURE_OPENAI_API_VERSION  optional

The agents pass Ollama-style options (`think`, `response_format="json"`). For the cloud providers a
chat middleware rewrites them into the portable form (drops `think`, maps "json" to a JSON-object
response format), so the agents, prompts and checkers stay identical across providers.

    .venv/bin/python -m insidegame.providers          # show the active provider and send one test prompt
"""
import os

from agent_framework import chat_middleware

PROVIDERS = ("ollama", "foundry", "azure_openai")
DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"


class ProviderConfigError(RuntimeError):
    """The chosen provider is missing configuration; the message says which variables to set."""


def provider_name():
    name = os.environ.get("INSIDEGAME_PROVIDER", "ollama").strip().lower().replace("-", "_")
    if name not in PROVIDERS:
        raise ProviderConfigError(f"INSIDEGAME_PROVIDER={name!r} is not one of {', '.join(PROVIDERS)}")
    return name


def _require(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise ProviderConfigError(
            f"{provider_name()} provider needs {', '.join(missing)}; see insidegame/providers.py for the variables")
    return [os.environ[n] for n in names]


@chat_middleware
async def portable_options(context, call_next):
    """Translate Ollama-only options so the same agent calls work against OpenAI-compatible endpoints."""
    options = dict(context.options or {})
    options.pop("think", None)
    if options.get("response_format") == "json":
        options["response_format"] = {"type": "json_object"}
    context.options = options
    await call_next()


def _credential():
    try:
        from azure.identity.aio import DefaultAzureCredential
    except ModuleNotFoundError as exc:
        raise ProviderConfigError("Entra ID auth needs the azure-identity package (pip install azure-identity)") from exc
    return DefaultAzureCredential()


def get_client(model=None):
    """Return an Agent Framework chat client for the configured provider.

    `model` overrides the model or deployment name from the environment (the CLIs pass `--model`)."""
    name = provider_name()
    if name == "ollama":
        from agent_framework.ollama import OllamaChatClient

        return OllamaChatClient(model=model or os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
                                host=os.environ.get("OLLAMA_HOST"))
    if name == "foundry":
        if model:
            os.environ.setdefault("FOUNDRY_MODEL", model)
        endpoint, deployment = _require("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL")
        from agent_framework.foundry import FoundryChatClient

        return FoundryChatClient(project_endpoint=endpoint, model=model or deployment,
                                 credential=_credential(), middleware=[portable_options])
    if model:
        os.environ.setdefault("AZURE_OPENAI_MODEL", model)
    endpoint, deployment = _require("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_MODEL")
    from agent_framework.openai import OpenAIChatCompletionClient

    key = os.environ.get("AZURE_OPENAI_API_KEY")
    return OpenAIChatCompletionClient(model=model or deployment, azure_endpoint=endpoint, api_key=key,
                                      credential=None if key else _credential(),
                                      api_version=os.environ.get("AZURE_OPENAI_API_VERSION"),
                                      middleware=[portable_options])


def model_label(model=None):
    """Label stored in bundles as the source of agent lines, e.g. "ollama:qwen3.5:9b" or "foundry:gpt-4.1-mini"."""
    name = provider_name()
    env = {"ollama": "OLLAMA_MODEL", "foundry": "FOUNDRY_MODEL", "azure_openai": "AZURE_OPENAI_MODEL"}[name]
    default = DEFAULT_OLLAMA_MODEL if name == "ollama" else "unset"
    return f"{name}:{model or os.environ.get(env) or default}"


async def _smoke(client):
    from agent_framework import Agent

    agent = Agent(client, 'Reply with JSON only: {"ok": true, "provider": "<who you are>"}.', name="smoke")
    response = await agent.run("Say hello.", options={"temperature": 0, "max_tokens": 40, "think": False,
                                                      "response_format": "json"})
    return response.text


def main():
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Show the configured model provider and send one test prompt.")
    parser.add_argument("--model")
    args = parser.parse_args()
    try:
        client = get_client(args.model)
        print("provider:", model_label(args.model))
        print("reply:", asyncio.run(_smoke(client)))
    except ProviderConfigError as exc:
        raise SystemExit(f"configuration error: {exc}")
    except Exception as exc:  # e.g. no Entra ID sign-in: keep the first line, the SDK message is long
        raise SystemExit(f"provider call failed: {str(exc).splitlines()[0][:300]}")


if __name__ == "__main__":
    main()
