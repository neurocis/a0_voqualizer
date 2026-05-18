from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin.yaml"
HOOKS = ROOT / "hooks.py"
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE = ROOT / "docs" / "release" / "v0.1.0.md"
README = ROOT / "README.md"
SKILL = ROOT / "SKILL.md"
REQUIREMENTS = ROOT / "requirements.txt"
DEFAULT_CONFIG = ROOT / "default_config.yaml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a85_release_artifacts_exist():
    for path in [PLUGIN, HOOKS, CHANGELOG, RELEASE, README, SKILL, REQUIREMENTS, DEFAULT_CONFIG]:
        assert path.is_file(), path


def test_plugin_yaml_declares_v010_installable_metadata():
    data = yaml.safe_load(read(PLUGIN))
    assert data["name"] == "a0_voqualizer"
    assert data["title"] == "A0 Voqualizer"
    assert data["version"] == "0.1.0"
    assert "settings_sections" in data
    assert "Real-time streaming WebSocket ASR/TTS bridge" in data["description"]


def test_hooks_expose_standard_a0_install_and_uninstall():
    text = read(HOOKS)
    assert "def install()" in text
    assert "def uninstall()" in text
    assert "STATUS_FILE" in text
    assert "_ensure_config_json" in text
    assert "requirements.txt" in text or "_REQUIREMENTS" in text


def test_changelog_documents_v010_release_scope_and_validation():
    text = read(CHANGELOG)
    assert "## v0.1.0" in text
    for marker in [
        "per-session `bearer_token`",
        "A2 4-byte audio frame",
        "Browser WebUI",
        "iOS Swift demo client",
        "Android Kotlin demo client",
        "Twilio Media Streams bridge",
        "Asterisk audio-fork bridge",
        "Security review",
        "32 concurrent session load harness",
        "Stable error taxonomy",
        "plugin.yaml",
        "hooks.py",
        "standard A0 plugin mechanism",
    ]:
        assert marker in text


def test_release_doc_covers_tag_changelog_and_installability():
    text = read(RELEASE)
    assert "A8.5" in text
    assert "v0.1.0" in text
    assert "git tag -a v0.1.0" in text
    assert "CHANGELOG.md" in text
    assert "plugin installable via standard A0 mechanism" in text
    assert "plugin.yaml" in text
    assert "hooks.py" in text
    assert "install()" in text
    assert "uninstall()" in text
    assert "requirements.txt" in text
    assert "default_config.yaml" in text


def test_readme_and_skill_reference_release_docs():
    readme = read(README)
    skill = read(SKILL)
    assert "v0.1.0" in readme
    assert "CHANGELOG.md" in readme
    assert "docs/protocol/errors.md" in skill
    assert "examples/README.md" in skill


def test_standard_plugin_files_are_present_for_installability():
    assert (ROOT / "api" / "ws_voqualizer.py").is_file()
    assert (ROOT / "api" / "voqualizer_admin.py").is_file()
    assert (ROOT / "webui" / "config.html").is_file()
    assert (ROOT / "webui" / "tester.html").is_file()
    assert (ROOT / "helpers" / "error_taxonomy.py").is_file()
