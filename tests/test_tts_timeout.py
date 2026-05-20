from pathlib import Path



def test_openai_tts_uses_client_timeout_object_marker():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/helpers/tts/openai_tts.py').read_text()
    assert '_client_timeout' in src
    assert 'ClientTimeout(total=float(timeout))' in src
    assert 'aiohttp.ClientSession(**kwargs)' in src
