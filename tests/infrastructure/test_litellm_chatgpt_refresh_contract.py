import importlib.util

import pytest


def test_litellm_chatgpt_authenticator_refresh_contract():
    if importlib.util.find_spec("litellm") is None:
        pytest.skip("LiteLLM is not installed")
    from litellm.llms.chatgpt.authenticator import Authenticator

    method = getattr(Authenticator, "_refresh_tokens", None)
    assert callable(method), (
        "snodo.infrastructure.chatgpt_oauth refresh depends on LiteLLM's private "
        "Authenticator._refresh_tokens(refresh_token); revisit the token module "
        "when this method is gone"
    )
    assert "refresh_token" in __import__("inspect").signature(method).parameters
