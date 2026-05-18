package com.a0.voqualizerdemo

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID
import kotlin.math.max
import kotlin.math.min

const val VOQUALIZER_SOCKET_HANDLER = "plugins/a0_voqualizer/ws_voqualizer"
const val VOQUALIZER_INPUT_CODEC = "pcm16/16k"
const val VOQUALIZER_OUTPUT_CODEC = "pcm16/16k"
const val VOQUALIZER_SAMPLE_RATE = 16_000
const val VOQUALIZER_CHANNELS = 1

interface VoqualizerTransport {
    val isConnected: Boolean
    suspend fun connect(handler: String)
    fun disconnect()
    suspend fun emitWithAck(event: String, payload: Map<String, Any>): Map<String, Any?>
    fun on(event: String, callback: (Map<String, Any?>) -> Unit)
}

data class VoqualizerUiState(
    val connected: Boolean = false,
    val capturing: Boolean = false,
    val playing: Boolean = false,
    val sessionId: String = "",
    val bearerToken: String = "",
    val partialText: String = "",
    val finalTranscripts: List<String> = emptyList(),
    val agentText: String = "",
    val eventLog: List<String> = emptyList(),
    val lastError: String? = null,
)

data class VoqualizerFrame(val seq: Int, val tsMs: Int, val pcm16: ByteArray) {
    fun encode(): ByteArray {
        val buffer = ByteBuffer.allocate(4 + pcm16.size).order(ByteOrder.BIG_ENDIAN)
        buffer.putShort((seq and 0xffff).toShort())
        buffer.putShort((tsMs and 0xffff).toShort())
        buffer.put(pcm16)
        return buffer.array()
    }
}

class VoqualizerClient(
    private val transport: VoqualizerTransport,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate),
) {
    private val _state = MutableStateFlow(VoqualizerUiState())
    val state: StateFlow<VoqualizerUiState> = _state

    private var seq = 0
    private var captureStartedAtMs = 0L
    private var captureJob: Job? = null
    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null

    init {
        bindServerEvents()
    }

    suspend fun connect(sessionId: String = UUID.randomUUID().toString()) {
        transport.connect(VOQUALIZER_SOCKET_HANDLER)
        val ready = transport.emitWithAck(
            "voqualizer_init",
            mapOf(
                "session_id" to sessionId,
                "asr" to mapOf("codec" to VOQUALIZER_INPUT_CODEC),
                "tts" to mapOf("codec" to VOQUALIZER_OUTPUT_CODEC),
                "barge_in" to true,
            ),
        )
        val token = ready["bearer_token"] as? String ?: throw VoqualizerClientError.MissingBearerToken
        _state.update {
            it.copy(
                connected = true,
                sessionId = (ready["session_id"] as? String) ?: sessionId,
                bearerToken = token,
                lastError = null,
                eventLog = appendLog(it.eventLog, "voqualizer_ready"),
            )
        }
    }

    suspend fun startFullDuplex() {
        ensureBearerToken()
        configurePlayback()
        startMicrophoneCapture()
        _state.update { it.copy(playing = true) }
    }

    suspend fun stopFullDuplex() {
        captureJob?.cancel()
        captureJob = null
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        audioTrack?.pause()
        audioTrack?.flush()
        audioTrack?.release()
        audioTrack = null
        _state.update { it.copy(capturing = false, playing = false) }
    }

    suspend fun sendText(text: String) {
        ensureBearerToken()
        val clean = text.trim()
        if (clean.isEmpty()) return
        transport.emitWithAck(
            "voqualizer_user_text",
            sessionPayload(
                mapOf(
                    "text" to clean,
                    "codec" to VOQUALIZER_OUTPUT_CODEC,
                    "sample_rate" to VOQUALIZER_SAMPLE_RATE,
                ),
            ),
        )
        log("voqualizer_user_text")
    }

    suspend fun control(action: String) {
        ensureBearerToken()
        transport.emitWithAck("voqualizer_control", sessionPayload(mapOf("action" to action)))
        log("voqualizer_control:$action")
        if (action == "end_session") {
            stopFullDuplex()
            _state.update { it.copy(connected = false, bearerToken = "") }
        }
    }

    fun handleEvent(event: String, payload: Map<String, Any?>) {
        when (event) {
            "voqualizer_asr_partial" -> _state.update { it.copy(partialText = payload["text"] as? String ?: "") }
            "voqualizer_asr_final" -> {
                val text = payload["text"] as? String ?: ""
                _state.update {
                    it.copy(
                        partialText = "",
                        finalTranscripts = if (text.isEmpty()) it.finalTranscripts else it.finalTranscripts + text,
                    )
                }
            }
            "voqualizer_agent_delta" -> {
                val delta = (payload["delta"] as? String) ?: (payload["text"] as? String) ?: ""
                _state.update { it.copy(agentText = it.agentText + delta) }
            }
            "voqualizer_agent_response_final" -> {
                val text = (payload["text"] as? String) ?: (payload["content"] as? String)
                if (text != null) _state.update { it.copy(agentText = text) }
            }
            "voqualizer_tts_chunk" -> {
                val audio = payload["audio"] as? ByteArray ?: ByteArray(0)
                val sampleRate = (payload["sample_rate"] as? Number)?.toInt() ?: VOQUALIZER_SAMPLE_RATE
                playPcm16(audio, sampleRate)
            }
            "voqualizer_tts_done" -> _state.update { it.copy(playing = false) }
            "voqualizer_error" -> _state.update { it.copy(lastError = payload["message"] as? String ?: payload["code"] as? String ?: "voqualizer_error") }
        }
        log(event)
    }

    private fun bindServerEvents() {
        listOf(
            "voqualizer_asr_partial",
            "voqualizer_asr_final",
            "voqualizer_agent_delta",
            "voqualizer_agent_response_final",
            "voqualizer_tts_chunk",
            "voqualizer_tts_done",
            "voqualizer_error",
        ).forEach { event -> transport.on(event) { payload -> handleEvent(event, payload) } }
    }

    private suspend fun startMicrophoneCapture() = withContext(Dispatchers.IO) {
        val minBuffer = AudioRecord.getMinBufferSize(
            VOQUALIZER_SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = max(minBuffer, 320 * 2)
        val recorder = AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(VOQUALIZER_SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(bufferSize)
            .build()
        audioRecord = recorder
        captureStartedAtMs = System.currentTimeMillis()
        seq = 0
        recorder.startRecording()
        _state.update { it.copy(capturing = true) }
        captureJob = scope.launch(Dispatchers.IO) {
            val buffer = ByteArray(320 * 2)
            while (true) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) {
                    val pcm = buffer.copyOf(read)
                    val elapsed = ((System.currentTimeMillis() - captureStartedAtMs).toInt() and 0xffff)
                    val frame = VoqualizerFrame(seq = seq, tsMs = elapsed, pcm16 = pcm).encode()
                    seq = (seq + 1) and 0xffff
                    try {
                        transport.emitWithAck("voqualizer_audio_chunk", sessionPayload(mapOf("frame" to frame)))
                    } catch (error: Throwable) {
                        _state.update { it.copy(lastError = error.message ?: error.toString()) }
                    }
                }
            }
        }
    }

    private fun configurePlayback() {
        if (audioTrack != null) return
        val minBuffer = AudioTrack.getMinBufferSize(
            VOQUALIZER_SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(VOQUALIZER_SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(max(minBuffer, 320 * 4))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        audioTrack?.play()
    }

    private fun playPcm16(audio: ByteArray, sampleRate: Int) {
        if (sampleRate != VOQUALIZER_SAMPLE_RATE) {
            // This reference demo negotiates pcm16/16k. Production apps can add resampling here.
        }
        configurePlayback()
        audioTrack?.write(audio, 0, audio.size)
        _state.update { it.copy(playing = true) }
    }

    private fun sessionPayload(payload: Map<String, Any>): Map<String, Any> = payload + ("bearer_token" to _state.value.bearerToken)

    private fun ensureBearerToken() {
        if (_state.value.bearerToken.isEmpty()) throw VoqualizerClientError.MissingBearerToken
    }

    private fun log(event: String) {
        _state.update { it.copy(eventLog = appendLog(it.eventLog, event)) }
    }

    private fun appendLog(events: List<String>, event: String): List<String> {
        val next = events + event
        return if (next.size > 200) next.takeLast(200) else next
    }
}

sealed class VoqualizerClientError(message: String) : Exception(message) {
    object MissingBearerToken : VoqualizerClientError("voqualizer_ready did not issue bearer_token")
}
