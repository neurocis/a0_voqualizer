"""Regression syntax checks for Voqualizer webui extension inline modules."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / 'extensions/webui/chat-input-box-end/voqualizer-buttons.html',
    ROOT / 'extensions/webui/chat-input-box-end/voqualizer-wyoming-buttons.html',
]


def test_inline_module_scripts_are_js_syntax_valid(tmp_path):
    for html in FILES:
        src = html.read_text()
        blocks = re.findall(r'<script[^>]*type="module"[^>]*>([\s\S]*?)</script>', src)
        assert blocks, html
        for idx, block in enumerate(blocks):
            path = tmp_path / f'{html.stem}-{idx}.mjs'
            path.write_text(block)
            result = subprocess.run(['node', '--check', str(path)], text=True, capture_output=True)
            assert result.returncode == 0, f'{html}:{idx}\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}'
