import pytest


@pytest.fixture(autouse=True)
def no_real_llm_calls(monkeypatch):
    """Tests must never call a real model. litellm loads .env on import, so a key
    in .env would otherwise make any un-faked plan/review call a paid API call."""
    import litellm

    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to call litellm.completion; pass a fake instead")

    monkeypatch.setattr(litellm, "completion", blocked)


@pytest.fixture(autouse=True)
def expert_env_restored(monkeypatch):
    """design() turns BrickWright's ask_expert on through the environment; undo it
    after each test."""
    monkeypatch.delenv("BRICKWRIGHT_EXPERT_CONTEXT", raising=False)
