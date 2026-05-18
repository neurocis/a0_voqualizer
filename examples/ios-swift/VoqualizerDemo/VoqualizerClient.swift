import AVFoundation
import Foundation

public let voqualizerSocketHandler = "plugins/a0_voqualizer/ws_voqualizer"
public let voqualizerInputCodec = "pcm16/16k"
public let voqualizerOutputCodec = "pcm16/16k"
public let voqualizerSampleRate: Double = 16_000

public protocol VoqualizerSocketTransport: AnyObject {
    var isConnected: Bool { get }
    func connect(handler: String) async throws
    func disconnect()
    func emitWithAck(_ event: String, _ payload: [String: Any]) async throws -> [String: Any]
    func on(_ event: String, _ callback: @escaping ([String: Any]) -> Void)
}

public struct VoqualizerTranscript: Identifiable, Equatable {
    public let id = UUID()
    public let text: String
    public let isFinal: Bool
}

public struct VoqualizerFrame {
    public let seq: UInt16
    public let tsMs: UInt16
    public let pcm16: Data

    public func encoded() -> Data {
        var data = Data(capacity: 4 + pcm16.count)
        data.append(UInt8((seq >> 8) & 0xff))
        data.append(UInt8(seq & 0xff))
        data.append(UInt8((tsMs >> 8) & 0xff))
        data.append(UInt8(tsMs & 0xff))
        data.append(pcm16)
        return data
    }
}

@MainActor
public final class VoqualizerClient: ObservableObject {
    @Published public private(set) var connected = false
    @Published public private(set) var capturing = false
    @Published public private(set) var playing = false
    @Published public private(set) var sessionId = ""
    @Published public private(set) var bearerToken = ""
    @Published public private(set) var partialText = ""
    @Published public private(set) var finalTranscripts: [VoqualizerTranscript] = []
    @Published public private(set) var agentText = ""
    @Published public private(set) var eventLog: [String] = []
    @Published public private(set) var lastError: String?

    private let transport: VoqualizerSocketTransport
    private let audioEngine = AVAudioEngine()
    private let playbackEngine = AVAudioEngine()
    private let playbackNode = AVAudioPlayerNode()
    private var seq: UInt16 = 0
    private var captureStart = Date()

    public init(transport: VoqualizerSocketTransport) {
        self.transport = transport
        bindServerEvents()
    }

    public func connect(sessionId requestedSessionId: String = UUID().uuidString) async throws {
        try await transport.connect(handler: voqualizerSocketHandler)
        let ready = try await transport.emitWithAck("voqualizer_init", [
            "session_id": requestedSessionId,
            "asr": ["codec": voqualizerInputCodec],
            "tts": ["codec": voqualizerOutputCodec],
            "barge_in": true,
        ])
        guard let issuedToken = ready["bearer_token"] as? String, !issuedToken.isEmpty else {
            throw VoqualizerClientError.missingBearerToken
        }
        self.sessionId = (ready["session_id"] as? String) ?? requestedSessionId
        self.bearerToken = issuedToken
        self.connected = true
        appendEvent("voqualizer_ready")
    }

    public func startFullDuplex() async throws {
        try ensureBearerToken()
        try configurePlayback()
        try startMicrophoneCapture()
        playing = true
    }

    public func stopFullDuplex() async {
        audioEngine.inputNode.removeTap(onBus: 0)
        audioEngine.stop()
        playbackNode.stop()
        playbackEngine.stop()
        capturing = false
        playing = false
    }

    public func sendText(_ text: String) async throws {
        try ensureBearerToken()
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        _ = try await transport.emitWithAck("voqualizer_user_text", sessionPayload([
            "text": clean,
            "codec": voqualizerOutputCodec,
            "sample_rate": Int(voqualizerSampleRate),
        ]))
        appendEvent("voqualizer_user_text")
    }

    public func control(_ action: String) async throws {
        try ensureBearerToken()
        _ = try await transport.emitWithAck("voqualizer_control", sessionPayload(["action": action]))
        appendEvent("voqualizer_control:\(action)")
        if action == "end_session" {
            await stopFullDuplex()
            connected = false
            bearerToken = ""
        }
    }

    public func handleEvent(_ event: String, payload: [String: Any]) {
        switch event {
        case "voqualizer_asr_partial":
            partialText = payload["text"] as? String ?? ""
        case "voqualizer_asr_final":
            let text = payload["text"] as? String ?? ""
            partialText = ""
            if !text.isEmpty {
                finalTranscripts.append(VoqualizerTranscript(text: text, isFinal: true))
            }
        case "voqualizer_agent_delta":
            agentText += (payload["delta"] as? String) ?? (payload["text"] as? String) ?? ""
        case "voqualizer_agent_response_final":
            agentText = (payload["text"] as? String) ?? (payload["content"] as? String) ?? agentText
        case "voqualizer_tts_chunk":
            if let data = payload["audio"] as? Data {
                playPcm16(data, sampleRate: payload["sample_rate"] as? Double ?? voqualizerSampleRate)
            }
        case "voqualizer_tts_done":
            playing = false
        case "voqualizer_error":
            lastError = (payload["message"] as? String) ?? (payload["code"] as? String) ?? "voqualizer_error"
        default:
            break
        }
        appendEvent(event)
    }

    private func bindServerEvents() {
        for event in [
            "voqualizer_asr_partial",
            "voqualizer_asr_final",
            "voqualizer_agent_delta",
            "voqualizer_agent_response_final",
            "voqualizer_tts_chunk",
            "voqualizer_tts_done",
            "voqualizer_error",
        ] {
            transport.on(event) { [weak self] payload in
                Task { @MainActor in self?.handleEvent(event, payload: payload) }
            }
        }
    }

    private func startMicrophoneCapture() throws {
        let input = audioEngine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        captureStart = Date()
        seq = 0
        input.installTap(onBus: 0, bufferSize: 320, format: inputFormat) { [weak self] buffer, _ in
            guard let self else { return }
            let pcm16 = Self.downmixAndResampleToPcm16(buffer: buffer, targetSampleRate: voqualizerSampleRate)
            let elapsedMs = UInt16(Int(Date().timeIntervalSince(self.captureStart) * 1000) & 0xffff)
            let frame = VoqualizerFrame(seq: self.seq, tsMs: elapsedMs, pcm16: pcm16).encoded()
            self.seq &+= 1
            Task { @MainActor in
                do {
                    try self.ensureBearerToken()
                    _ = try await self.transport.emitWithAck("voqualizer_audio_chunk", self.sessionPayload(["frame": frame]))
                } catch {
                    self.lastError = String(describing: error)
                }
            }
        }
        audioEngine.prepare()
        try audioEngine.start()
        capturing = true
    }

    private func configurePlayback() throws {
        if playbackEngine.attachedNodes.contains(playbackNode) == false {
            playbackEngine.attach(playbackNode)
        }
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: voqualizerSampleRate, channels: 1, interleaved: false)!
        playbackEngine.connect(playbackNode, to: playbackEngine.mainMixerNode, format: format)
        playbackEngine.prepare()
        if !playbackEngine.isRunning {
            try playbackEngine.start()
        }
        if !playbackNode.isPlaying {
            playbackNode.play()
        }
    }

    private func playPcm16(_ data: Data, sampleRate: Double) {
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false) else { return }
        let frameCount = AVAudioFrameCount(data.count / 2)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else { return }
        buffer.frameLength = frameCount
        data.withUnsafeBytes { raw in
            let int16 = raw.bindMemory(to: Int16.self)
            let channel = buffer.floatChannelData![0]
            for i in 0..<Int(frameCount) {
                let sample = Int16(littleEndian: int16[i])
                channel[i] = sample < 0 ? Float(sample) / 32768.0 : Float(sample) / 32767.0
            }
        }
        playbackNode.scheduleBuffer(buffer, completionHandler: nil)
        playing = true
    }

    private func sessionPayload(_ payload: [String: Any]) -> [String: Any] {
        var next = payload
        next["bearer_token"] = bearerToken
        return next
    }

    private func ensureBearerToken() throws {
        if bearerToken.isEmpty {
            throw VoqualizerClientError.missingBearerToken
        }
    }

    private func appendEvent(_ event: String) {
        eventLog.append(event)
        if eventLog.count > 200 {
            eventLog.removeFirst(eventLog.count - 200)
        }
    }

    public static func downmixAndResampleToPcm16(buffer: AVAudioPCMBuffer, targetSampleRate: Double) -> Data {
        guard let floatData = buffer.floatChannelData else { return Data() }
        let channels = Int(buffer.format.channelCount)
        let inputFrames = Int(buffer.frameLength)
        guard inputFrames > 0 else { return Data() }

        let ratio = buffer.format.sampleRate / targetSampleRate
        let outputFrames = max(1, Int(Double(inputFrames) / ratio))
        var output = Data(capacity: outputFrames * 2)

        for outIndex in 0..<outputFrames {
            let inputIndex = min(inputFrames - 1, Int(Double(outIndex) * ratio))
            var mono: Float = 0
            for channel in 0..<channels {
                mono += floatData[channel][inputIndex]
            }
            mono /= Float(max(1, channels))
            let clamped = max(-1.0, min(1.0, mono))
            let scaled = clamped < 0 ? Int16(clamped * 32768.0) : Int16(clamped * 32767.0)
            var little = scaled.littleEndian
            output.append(UnsafeBufferPointer(start: &little, count: 1))
        }
        return output
    }
}

public enum VoqualizerClientError: Error, Equatable {
    case missingBearerToken
}
