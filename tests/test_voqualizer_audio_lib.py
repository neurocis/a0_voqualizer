"""Verify shared webui/lib/voqualizer-audio.js helpers exist."""
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
LIB = PLUGIN / 'webui' / 'lib' / 'voqualizer-audio.js'
CM = PLUGIN / 'webui' / 'conversation-mode.js'


def test_lib_exists_with_expected_exports():
    assert LIB.exists()
    s = LIB.read_text()
    for marker in (
        'export function framePcm16',
        'export function audioChunkPayload',
        'export function pcm16ToFloat32',
        'export function alignPcm16Bytes',
        'export function clearPcm16Carry',
        'export function createPlaybackTracker',
        'export function maybeLocalBargeInFromMic',
        'export async function fetchSessionToken',
        'export async function initMicWorklet',
        'FRAME_HEADER_BYTES',
        'INPUT_CODEC',
        'OUTPUT_CODEC',
    ):
        assert marker in s, f'missing export marker {marker!r}'


def test_conversation_mode_imports_from_shared_lib():
    s = CM.read_text()
    assert "from '/plugins/a0_voqualizer/webui/lib/voqualizer-audio.js'" in s


def test_tts_payload_base64_helpers_present():
    s = LIB.read_text()
    for marker in (
        'export function base64ToBytes',
        'export function bytesFromTtsPayload',
        'audio_b64',
        'atob(text)',
        'bytesFromUnknownAudio(data.audio_bytes || data.audio || data.pcm16',
    ):
        assert marker in s, f'missing TTS payload helper marker {marker!r}'
