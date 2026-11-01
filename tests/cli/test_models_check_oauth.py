from snodo.cli.commands import models_check


def test_oauth_model_check_signin_and_call_results(monkeypatch, capsys):
    from snodo.config import ConfigManager
    from snodo.infrastructure import chatgpt_oauth

    monkeypatch.setattr(ConfigManager, "_provider_for_model", staticmethod(lambda model: "chatgpt" if model.startswith("chatgpt/") else "openai"))
    monkeypatch.setattr(ConfigManager, "provider_uses_oauth", lambda self, provider: provider == "chatgpt")
    monkeypatch.setattr(chatgpt_oauth, "ChatGPTReauthenticationRequired", type("ChatGPTReauthenticationRequired", (Exception,), {
        "__init__": lambda self, provider: Exception.__init__(self, f"Run snodo models --provider {provider} --authenticate")
    }))

    state = {"signed": False}
    monkeypatch.setattr(chatgpt_oauth, "chatgpt_sign_in_state", lambda: {"signed_in": True} if state["signed"] else None)
    calls = []

    def canary(model, role):
        calls.append(model)
        if model == "chatgpt/failing":
            raise chatgpt_oauth.ChatGPTReauthenticationRequired("chatgpt")
        if model == "chatgpt/bad":
            raise RuntimeError("provider error")

    def check(model):
        return models_check.check_configured_models(lambda: [("validator", model)], canary)

    assert check("chatgpt/missing") == 1
    assert calls == []
    assert "--provider chatgpt --authenticate" in capsys.readouterr().out

    state["signed"] = True
    assert check("chatgpt/failing") == 1
    assert "--provider chatgpt --authenticate" in capsys.readouterr().out
    assert check("chatgpt/ok") == 0
    assert "OK" in capsys.readouterr().out
    assert check("chatgpt/bad") == 1
    assert "provider error" in capsys.readouterr().out
    assert check("openai/keyed") == 0
    assert "OK" in capsys.readouterr().out
