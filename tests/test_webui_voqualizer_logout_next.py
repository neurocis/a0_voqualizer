"""Voqualizer standalone logout should preserve return-to-Voqualizer next target."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / 'webui' / 'voqualizer.html'
JS = ROOT / 'webui' / 'voqualizer.js'


def test_logout_anchor_has_voqualizer_next_fallback():
    src = HTML.read_text()
    assert 'id="voq-logout-button"' in src
    assert 'href="/logout?next=%2Fplugins%2Fa0_voqualizer%2Fwebui%2Fvoqualizer.html"' in src
    assert 'w60-logout-next-2026-06-09-1' in src


def test_logout_js_sets_dynamic_next_back_to_current_voq_page():
    src = JS.read_text()
    for marker in (
        "function voqualizerCanonicalPathWithSearch()",
        "function voqualizerLoginNextUrl()",
        "function voqualizerLogoutUrl()",
        "function bindLogoutNextRedirect(logout)",
        "encodeURIComponent(voqualizerCanonicalPathWithSearch())",
        "logout.setAttribute('href', logoutUrl)",
        "logout.dataset.loginNext",
        "lastLoginNextHref",
        "const PAGE_VERSION = 'w60-logout-next-2026-06-09-1'",
    ):
        assert marker in src, marker


def test_logout_next_exports_for_regression_visibility():
    src = JS.read_text()
    for marker in (
        'voqualizerCanonicalPathWithSearch,',
        'voqualizerLoginNextUrl,',
        'voqualizerLogoutUrl,',
        'bindLogoutNextRedirect,',
    ):
        assert marker in src
