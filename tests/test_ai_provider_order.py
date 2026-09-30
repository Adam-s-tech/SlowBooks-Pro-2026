"""Claude and Grok come first in the AI provider picker, side by side: the two
agents SlowBooks is built for, as the README, the docs and the website list
them (#200). The settings page pre-selects the first provider on an install
that hasn't chosen one; a saved choice is kept."""

from pathlib import Path

from app.services.ai_service import PROVIDERS, provider_list

ROOT = Path(__file__).resolve().parents[1]


def test_claude_and_grok_come_first():
    keys = [p["key"] for p in provider_list()]
    assert keys[:2] == ["anthropic", "grok"]
    # every provider is still offered, once
    assert sorted(keys) == sorted(PROVIDERS) and len(keys) == len(set(keys))


def test_a_new_install_starts_on_the_first_and_a_saved_choice_is_kept():
    js = (ROOT / "app" / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    assert "cfg.provider || (providers[0] && providers[0].key)" in js


def test_the_ai_config_lists_them_in_that_order(client):
    body = client.get("/api/analytics/ai-config").json()
    assert [p["key"] for p in body["providers"]][:2] == ["anthropic", "grok"]
    assert body["provider"] == ""  # nothing chosen yet: the page shows Claude
