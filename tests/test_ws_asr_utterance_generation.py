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


def test_asr_final_reset_reuses_complete_state_factory():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'def _new_asr_utterance_state' in src
    assert 'state = self._new_asr_utterance_state(session)' in src
    assert 'state.update(self._new_asr_utterance_state(session))' in src
    assert 'asr_utterance_state_reset_at_ms' in src
    factory_start = src.index('def _new_asr_utterance_state')
    factory_end = src.index('def _asr_utterance_state_for_session')
    factory = src[factory_start:factory_end]
    for marker in ('preroll_chunks', 'has_speech', 'first_seq', 'speech_start_seq', 'preroll_chunks_merged', 'segment_first_seq'):
        assert marker in factory


def test_always_on_leading_audio_ring_markers():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'leading_audio_chunks' in src
    assert 'leading_audio_chunks.append(chunk)' in src
    assert 'leading_snapshot = list(leading_audio_chunks)' in src
    assert 'chunks.extend(leading_snapshot)' in src
    assert '_dedupe_audio_chunks_by_seq' in src
    for marker in (
        'leading_chunks_available',
        'leading_chunks_merged',
        'leading_first_seq',
        'leading_last_seq',
        'leading_ring_ms',
    ):
        assert marker in src


def test_ws_init_reads_provider_asr_preroll():
    from pathlib import Path
    src = Path('/a0/usr/plugins/a0_voqualizer/api/ws_voqualizer.py').read_text()
    assert 'asr_providers[asr_provider].get("asr_preroll_ms", 600.0)' in src
    assert 'session.metadata["asr_preroll_ms"] = asr_preroll_ms' in src
    assert 'asr_preroll_ms must be between 0 and 3000 ms' in src
    assert '"asr_preroll_ms": asr_preroll_ms' in src



def test_frontend_prompt_mode_skips_context_bridge_injection():
    s = WS.read_text()
    assert 'asr_submit_mode' in s
    assert 'frontend_prompt' in s
    assert 'context_injection_skipped' in s
    assert 'session.metadata["asr_submit_mode"]' in s
    assert 'bridge.inject_transcript' in s
    skip_idx = s.find('context_injection_skipped')
    inject_idx = s.find('bridge.inject_transcript')
    assert skip_idx != -1 and inject_idx != -1 and skip_idx < inject_idx
