"""Provider selection by environment; no model is called."""
import asyncio
from types import SimpleNamespace

import pytest

from insidegame import providers

ENV = ("INSIDEGAME_PROVIDER", "OLLAMA_MODEL", "OLLAMA_HOST", "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL",
       "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_VERSION")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_default_is_local_ollama():
    from agent_framework.ollama import OllamaChatClient

    assert isinstance(providers.get_client(), OllamaChatClient)
    assert providers.model_label() == "ollama:qwen3.5:9b"
    assert providers.model_label("llama3.2") == "ollama:llama3.2"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("INSIDEGAME_PROVIDER", "bedrock")
    with pytest.raises(providers.ProviderConfigError, match="not one of"):
        providers.get_client()


@pytest.mark.parametrize("name, needs", [("foundry", "FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL"),
                                         ("azure_openai", "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_MODEL")])
def test_cloud_providers_name_the_missing_variables(monkeypatch, name, needs):
    monkeypatch.setenv("INSIDEGAME_PROVIDER", name)
    with pytest.raises(providers.ProviderConfigError, match=needs):
        providers.get_client()


def test_foundry_client_is_built_from_the_environment(monkeypatch):
    from agent_framework.foundry import FoundryChatClient

    monkeypatch.setenv("INSIDEGAME_PROVIDER", "foundry")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("FOUNDRY_MODEL", "gpt-4.1-mini")
    assert isinstance(providers.get_client(), FoundryChatClient)  # no network until the first request
    assert providers.model_label() == "foundry:gpt-4.1-mini"


def test_azure_openai_client_with_key(monkeypatch):
    from agent_framework.openai import OpenAIChatCompletionClient

    monkeypatch.setenv("INSIDEGAME_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key-not-real")
    assert isinstance(providers.get_client(), OpenAIChatCompletionClient)


def test_portable_options_rewrites_ollama_only_options():
    seen = {}
    context = SimpleNamespace(options={"temperature": 0, "think": False, "response_format": "json"})

    async def call_next():
        seen.update(context.options)

    asyncio.run(providers.portable_options(context, call_next))
    assert seen == {"temperature": 0, "response_format": {"type": "json_object"}}
