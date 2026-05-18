import SwiftUI

public struct VoqualizerDemoView: View {
    @StateObject private var client: VoqualizerClient
    @State private var text = "Hello from iOS"

    public init(transport: VoqualizerSocketTransport) {
        _client = StateObject(wrappedValue: VoqualizerClient(transport: transport))
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("A0 Voqualizer iOS Demo").font(.title)
            Text("Connected: \(client.connected.description)")
            Text("Capturing: \(client.capturing.description)")
            Text("Session: \(client.sessionId.isEmpty ? "—" : client.sessionId)")
            Text("Bearer token: \(client.bearerToken.isEmpty ? "not issued" : "issued")")

            HStack {
                Button("Connect") { Task { try await client.connect(sessionId: "ios-demo") } }
                Button("Start full duplex") { Task { try await client.startFullDuplex() } }
                Button("Barge-in") { Task { try await client.control("barge_in") } }
            }

            HStack {
                TextField("Text to synthesize", text: $text)
                Button("Send text") { Task { try await client.sendText(text) } }
            }

            GroupBox("ASR") {
                VStack(alignment: .leading) {
                    Text("Partial: \(client.partialText)")
                    ForEach(client.finalTranscripts) { transcript in
                        Text("Final: \(transcript.text)")
                    }
                }
            }

            GroupBox("Agent") {
                ScrollView { Text(client.agentText).frame(maxWidth: .infinity, alignment: .leading) }
            }

            GroupBox("Events") {
                ScrollView { Text(client.eventLog.joined(separator: "\n")).frame(maxWidth: .infinity, alignment: .leading) }
            }
        }
        .padding()
    }
}
