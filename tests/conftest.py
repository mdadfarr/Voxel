import pytest


@pytest.fixture(autouse=True)
def no_real_llm_calls(monkeypatch):
    """Tests must never call a real model. litellm loads .env on import, so a key
    in .env would otherwise make any un-faked plan/review call a paid API call."""
    import litellm

    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to call litellm.completion; pass a fake instead")

    monkeypatch.setattr(litellm, "completion", blocked)
