package com.a0.voqualizerdemo

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Replace PlaceholderTransport with your Socket.IO-backed transport.
        val client = VoqualizerClient(PlaceholderTransport())
        setContent { VoqualizerDemoScreen(client) }
    }
}

@Composable
fun VoqualizerDemoScreen(client: VoqualizerClient) {
    val state by client.state.collectAsState()
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("Hello from Android") }

    Column(Modifier.padding(16.dp).verticalScroll(rememberScrollState())) {
        Text("A0 Voqualizer Android Demo")
        Text("Connected: ${state.connected}")
        Text("Capturing: ${state.capturing}")
        Text("Session: ${state.sessionId.ifEmpty { "—" }}")
        Text("Bearer token: ${if (state.bearerToken.isEmpty()) "not issued" else "issued"}")
        state.lastError?.let { Text("Error: $it") }

        Row {
            Button(onClick = { scope.launch { client.connect("android-demo") } }) { Text("Connect") }
            Button(onClick = { scope.launch { client.startFullDuplex() } }) { Text("Start full duplex") }
            Button(onClick = { scope.launch { client.control("barge_in") } }) { Text("Barge-in") }
        }

        Row {
            OutlinedTextField(value = text, onValueChange = { text = it }, label = { Text("Text") })
            Button(onClick = { scope.launch { client.sendText(text) } }) { Text("Send text") }
        }

        Text("ASR partial: ${state.partialText}")
        Text("ASR finals:\n${state.finalTranscripts.joinToString("\n")}")
        Text("Agent:\n${state.agentText}")
        Text("Events:\n${state.eventLog.joinToString("\n")}")
    }
}

class PlaceholderTransport : VoqualizerTransport {
    override val isConnected: Boolean = false
    override suspend fun connect(handler: String) { error("Wire this demo to Socket.IO with handler=$handler") }
    override fun disconnect() = Unit
    override suspend fun emitWithAck(event: String, payload: Map<String, Any>): Map<String, Any?> = error("No transport wired")
    override fun on(event: String, callback: (Map<String, Any?>) -> Unit) = Unit
}
