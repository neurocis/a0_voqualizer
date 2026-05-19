"""Source-level regression tests for ASR utterance generation/pre-roll guards."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
WS = PLUGIN / 'api' / 'ws_voqualizer.py'
FINAL = PLUGIN / 'helpers' / 'agent_finalizer.py'
CHUNKER = PLUGIN / 'helpers' / 'sentence_chunker.py'


def test_asr_utterance_generation_and_preroll_markers_present():
    s = WS.read_text()
    for marker in (
        'preroll_chunks',
        'chunks.extend(preroll_chunks)',
        'preroll_chunks.clear()',
        'asr_utterance_generation',
        'asr_current_utterance_id',
        'utterance_generation',
        'utterance_id',
        'stale_asr_result_ignored',
        'asr_last_stale_generation',
        'asr_last_stale_final_text',
        'final_reason',
        'is_forced_final',
        'chunk_is_final',
        'buffered_chunks',
        'first_seq',
        'speech_start_seq',
        'last_seq',
        'asr_preroll_ms',
    ):
        assert marker in s, f'missing ASR generation marker {marker!r}'


def test_only_final_transcripts_are_injected_to_context():
    s = WS.read_text()
    assert 'if event == "voqualizer_asr_final"' in s
    assert 'bridge.inject_transcript' in s
    assert 'voqualizer_asr_partial' not in s[s.find('bridge.inject_transcript') - 300:s.find('bridge.inject_transcript') + 300]


def test_tts_skip_diagnostics_are_emitted_or_recorded():
    final = FINAL.read_text()
    chunker = CHUNKER.read_text()
    for marker in ('empty_speech_text', 'tts_disabled', 'provider_error', 'tts_last_skip_reason'):
        assert marker in final, f'missing finalizer TTS diagnostic marker {marker!r}'
    assert 'tts_last_skip_reason' in chunker


def test_structural_preroll_merge_metadata_markers():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'asr_preroll_ms", 600.0' in src
    assert 'preroll_snapshot = list(preroll_chunks)' in src
    assert 'chunks.extend(preroll_snapshot)' in src
    assert 'preroll_chunks_available' in src
    assert 'preroll_chunks_merged' in src
    assert 'preroll_merged' in src
    assert 'segment_first_seq' in src
    assert 'segment_last_seq' in src
    assert 'asr_last_final_metadata' in src
