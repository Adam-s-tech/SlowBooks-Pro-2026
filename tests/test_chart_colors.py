"""Chart colours clear 3:1 in both themes (2.18.0).

A chart's lines, bars and legend keys are graphics, which need 3:1 against
the card (WCAG 1.4.11). The charts drew fixed bright colours, which read
well on the dark card and too faint on the light one (#00c48f 2.26:1,
#ff6b6b 2.78, #facc15 1.53). Each chart colour is a --chart-* colour the
stylesheets set per theme: legend keys use it directly, canvases read it
when they draw, and every one clears 3:1 on its theme's card. The same pass
moved three pieces of text off #00c48f (the AI settings' "saved" and "OK"
and the safety callout)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "app" / "static" / "css"
JS = ROOT / "app" / "static" / "js"
OLD = (
    "#00c48f",
    "#ff6b6b",
    "#ffa94d",
    "#ff922b",
    "#ff4757",
    "#5b7fff",
    "#a855f7",
    "#e879f9",
    "#38bdf8",
    "#facc15",
)


def _lum(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _chart_vars(text):
    return dict(re.findall(r"--chart-(\w+):\s*(#[0-9a-fA-F]{6})", text))


def test_every_chart_colour_clears_3_to_1_on_its_card():
    light = _chart_vars((CSS / "style.css").read_text(encoding="utf-8"))
    dark = _chart_vars((CSS / "dark.css").read_text(encoding="utf-8"))
    assert set(light) == set(dark) and len(light) >= 10
    low = {
        n: round(_ratio(c, "#ffffff"), 2)
        for n, c in light.items()
        if _ratio(c, "#ffffff") < 3
    }
    low.update(
        {
            f"dark {n}": round(_ratio(c, "#1e2028"), 2)
            for n, c in dark.items()
            if _ratio(c, "#1e2028") < 3
        }
    )
    assert low == {}


def test_the_charts_draw_the_themes_colours():
    for name in ("analytics.js", "dashboard.js"):
        text = (JS / name).read_text(encoding="utf-8")
        assert [c for c in OLD if c in text.lower()] == [], name
    dash = (JS / "dashboard.js").read_text(encoding="utf-8")
    assert "chip('var(--chart-red)')" in dash and "chartColor('green')" in dash
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "getPropertyValue('--chart-' + name)" in utils


def test_no_text_is_painted_the_faint_chart_green():
    css = (CSS / "style.css").read_text(encoding="utf-8")
    for rule in (".ai-key-saved", ".ai-test-ok", ".ai-worker-security"):
        body = css[css.index(rule) :]
        body = body[: body.index("}")]
        assert "#00c48f" not in body.split("/*")[0], rule
