package com.realtimeinput.client

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.inputmethod.BaseInputConnection
import android.widget.Button
import android.widget.EditText
import android.widget.RadioGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.ArrayDeque
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {
    companion object {
        private const val INPUT_SEND_DEBOUNCE_MS = 25L
        private const val COMPOSING_RETRY_DELAY_MS = 40L
        private const val APPEND_ONLY_WARNING = "Only append-at-cursor sync is supported"
        private const val PREFS_NAME = "rtcs_prefs"
        private const val PREF_PASTE_MODE = "paste_mode"
        private const val PASTE_MODE_CTRL_V = "ctrl_v"
        private const val PASTE_MODE_CTRL_SHIFT_V = "ctrl_shift_v"
        private const val PASTE_MODE_SHIFT_INSERT = "shift_insert"
    }

    private lateinit var hostEditText: EditText
    private lateinit var portEditText: EditText
    private lateinit var clearReconnectButton: Button
    private lateinit var connectButton: Button
    private lateinit var statusTextView: TextView
    private lateinit var latencyTextView: TextView
    private lateinit var ackTextView: TextView
    private lateinit var inputEditText: EditText
    private lateinit var pasteModeRadioGroup: RadioGroup

    private val clientId: String = "android-" + UUID.randomUUID().toString()
    private val uiHandler = Handler(Looper.getMainLooper())
    private val okHttpClient = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var isConnected = false
    private var isAuthed = false
    private var reconnectAttempt = 0
    private var shouldReconnect = false

    private var sessionId: String = ""
    private var token: String = ""
    private var heartbeatIntervalMs: Long = 5000L
    private var localSeq: Int = 0
    private var sentCount: Int = 0
    private var selectedPasteMode: String = PASTE_MODE_CTRL_V
    private var lastInputSnapshot: String = ""
    private var isProgrammaticInputChange: Boolean = false
    private val pendingAppends: ArrayDeque<String> = ArrayDeque()
    private val sentAtBySeq = ConcurrentHashMap<Int, Long>()
    private val pingSentAtByTs = ConcurrentHashMap<Long, Long>()
    private val processInputRunnable = Runnable { maybeProcessInputText() }

    private val reconnectRunnable = object : Runnable {
        override fun run() {
            if (!shouldReconnect || isConnected) return
            connectInternal()
        }
    }

    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            if (!isConnected || !isAuthed) return
            val ts = System.currentTimeMillis()
            val ping = JSONObject()
                .put("type", "ping")
                .put("session_id", sessionId)
                .put("token", token)
                .put("ts", ts)
            pingSentAtByTs[ts] = ts
            webSocket?.send(ping.toString())
            uiHandler.postDelayed(this, heartbeatIntervalMs)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        hostEditText = findViewById(R.id.hostEditText)
        portEditText = findViewById(R.id.portEditText)
        clearReconnectButton = findViewById(R.id.clearReconnectButton)
        connectButton = findViewById(R.id.connectButton)
        statusTextView = findViewById(R.id.statusTextView)
        latencyTextView = findViewById(R.id.latencyTextView)
        ackTextView = findViewById(R.id.ackTextView)
        inputEditText = findViewById(R.id.inputEditText)
        pasteModeRadioGroup = findViewById(R.id.pasteModeRadioGroup)

        selectedPasteMode = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getString(PREF_PASTE_MODE, PASTE_MODE_CTRL_V)
            ?.takeIf { it in setOf(PASTE_MODE_CTRL_V, PASTE_MODE_CTRL_SHIFT_V, PASTE_MODE_SHIFT_INSERT) }
            ?: PASTE_MODE_CTRL_V
        bindPasteModeSelection()

        connectButton.setOnClickListener {
            if (isConnected) {
                disconnectManual()
            } else {
                shouldReconnect = true
                connectInternal()
            }
        }

        clearReconnectButton.setOnClickListener {
            clearLocalInput()
        }

        inputEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}

            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                if (isProgrammaticInputChange) {
                    lastInputSnapshot = s?.toString().orEmpty()
                    return
                }
                scheduleProcessInputText(INPUT_SEND_DEBOUNCE_MS)
            }

            override fun afterTextChanged(s: Editable?) {
                if (!isProgrammaticInputChange) {
                    scheduleProcessInputText(INPUT_SEND_DEBOUNCE_MS)
                }
            }
        })
    }

    override fun onDestroy() {
        super.onDestroy()
        shouldReconnect = false
        uiHandler.removeCallbacks(heartbeatRunnable)
        uiHandler.removeCallbacks(reconnectRunnable)
        webSocket?.close(1000, "Activity destroy")
        okHttpClient.dispatcher.executorService.shutdown()
    }

    private fun connectInternal() {
        val host = hostEditText.text.toString().trim()
        val port = portEditText.text.toString().trim().ifEmpty { "8765" }
        if (host.isEmpty()) {
            statusTextView.text = "Host IP required"
            return
        }
        val url = "ws://$host:$port/ws"
        updateStatus("Connecting: $url")
        val req = Request.Builder().url(url).build()
        webSocket = okHttpClient.newWebSocket(req, SocketListener())
    }

    private fun disconnectManual() {
        shouldReconnect = false
        uiHandler.removeCallbacks(heartbeatRunnable)
        uiHandler.removeCallbacks(reconnectRunnable)
        webSocket?.close(1000, "Manual disconnect")
        onDisconnected("Disconnected")
    }

    private fun clearLocalInput() {
        uiHandler.removeCallbacks(processInputRunnable)
        isProgrammaticInputChange = true
        inputEditText.setText("")
        isProgrammaticInputChange = false
        lastInputSnapshot = ""
        sentCount = 0
        pendingAppends.clear()
        sentAtBySeq.clear()
        pingSentAtByTs.clear()
        ackTextView.text = "ACK: -"
        updateStatus("Local input cleared")
    }

    private fun maybeProcessInputText() {
        val editable = inputEditText.text
        if (editable != null && BaseInputConnection.getComposingSpanStart(editable) != -1) {
            scheduleProcessInputText(COMPOSING_RETRY_DELAY_MS)
            return
        }
        val current = editable?.toString().orEmpty()
        if (current == lastInputSnapshot) return
        if (current.contains("\n") || current.contains("\r") || current.contains("\t") || current.contains("\b")) {
            statusTextView.text = "Control chars are not supported in MVP"
            restoreInputSnapshot()
            return
        }
        if (!current.startsWith(lastInputSnapshot)) {
            updateStatus(APPEND_ONLY_WARNING)
            restoreInputSnapshot()
            return
        }
        val suffix = current.substring(lastInputSnapshot.length)
        if (suffix.isNotEmpty()) {
            sendOrQueueTextInsert(suffix)
        }
        lastInputSnapshot = current
    }

    private fun restoreInputSnapshot() {
        isProgrammaticInputChange = true
        inputEditText.setText(lastInputSnapshot)
        inputEditText.setSelection(lastInputSnapshot.length)
        isProgrammaticInputChange = false
    }

    private fun scheduleProcessInputText(delayMs: Long) {
        uiHandler.removeCallbacks(processInputRunnable)
        uiHandler.postDelayed(processInputRunnable, delayMs)
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        reconnectAttempt += 1
        val backoffMs = minOf(5000L, (500L shl (reconnectAttempt - 1).coerceAtMost(3)))
        updateStatus("Reconnecting in ${backoffMs}ms")
        uiHandler.removeCallbacks(reconnectRunnable)
        uiHandler.postDelayed(reconnectRunnable, backoffMs)
    }

    private fun onDisconnected(reason: String) {
        isConnected = false
        isAuthed = false
        sessionId = ""
        token = ""
        localSeq = 0
        sentCount = 0
        lastInputSnapshot = inputEditText.text?.toString().orEmpty()
        sentAtBySeq.clear()
        pingSentAtByTs.clear()
        connectButton.text = getString(R.string.connect)
        updateStatus(reason)
    }

    private fun sendHelloAndAuth() {
        val hello = JSONObject()
            .put("type", "hello")
            .put("client_id", clientId)
            .put("app_ver", "1.0.0")
        webSocket?.send(hello.toString())
        val auth = JSONObject()
            .put("type", "auth")
            .put("paste_mode", selectedPasteMode)
        webSocket?.send(auth.toString())
    }

    private fun sendOrQueueTextInsert(text: String) {
        if (text.isEmpty()) return
        if (!isConnected || !isAuthed || sessionId.isEmpty() || token.isEmpty()) {
            pendingAppends.addLast(text)
            updateStatus("Append queued, waiting reconnect")
            return
        }
        val seq = localSeq + 1
        val now = System.currentTimeMillis()
        val msg = JSONObject()
            .put("type", "text_insert")
            .put("session_id", sessionId)
            .put("token", token)
            .put("seq", seq)
            .put("text", text)
            .put("paste_mode", selectedPasteMode)
            .put("ts", now)
        val sent = webSocket?.send(msg.toString()) == true
        if (sent) {
            localSeq = seq
            sentCount += text.length
            sentAtBySeq[seq] = now
        } else {
            pendingAppends.addFirst(text)
            updateStatus("Append queued, waiting reconnect")
        }
    }

    private fun flushPendingAppends() {
        while (pendingAppends.isNotEmpty() && isConnected && isAuthed && sessionId.isNotEmpty() && token.isNotEmpty()) {
            val beforeSeq = localSeq
            val append = pendingAppends.removeFirst()
            sendOrQueueTextInsert(append)
            if (localSeq == beforeSeq) {
                break
            }
        }
    }

    private fun handleMessage(text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "hello_ok" -> updateStatus("Hello ok, waiting auth...")
            "auth_ok" -> {
                sessionId = obj.optString("session_id")
                token = obj.optString("token")
                heartbeatIntervalMs = obj.optLong("heartbeat_interval_ms", 5000L)
                val serverPasteMode = obj.optString("paste_mode", selectedPasteMode)
                if (serverPasteMode.isNotEmpty()) {
                    selectedPasteMode = serverPasteMode
                    setPasteModeSelection(serverPasteMode)
                }
                isAuthed = sessionId.isNotEmpty() && token.isNotEmpty()
                if (isAuthed) {
                    reconnectAttempt = 0
                    updateStatus("Connected and authenticated")
                    uiHandler.removeCallbacks(heartbeatRunnable)
                    uiHandler.postDelayed(heartbeatRunnable, heartbeatIntervalMs)
                    flushPendingAppends()
                } else {
                    updateStatus("Auth failed: invalid session")
                }
            }
            "ack" -> {
                val seq = obj.optInt("seq", -1)
                val ok = obj.optBoolean("ok", false)
                val reason = obj.optString("reason")
                val sentAt = sentAtBySeq.remove(seq)
                val now = System.currentTimeMillis()
                val rtt = if (sentAt != null) (now - sentAt) else -1L
                ackTextView.text = if (ok) {
                    if (rtt >= 0) "ACK: seq=$seq ok, ${rtt}ms, sent=$sentCount" else "ACK: seq=$seq ok, sent=$sentCount"
                } else {
                    "ACK: seq=$seq failed: $reason"
                }
            }
            "pong" -> {
                val ts = obj.optLong("ts", -1L)
                val sentAt = pingSentAtByTs.remove(ts)
                if (sentAt != null) {
                    val rtt = System.currentTimeMillis() - sentAt
                    latencyTextView.text = "RTT: ${rtt}ms"
                }
            }
            "error" -> {
                updateStatus("Server error: ${obj.optString("reason")}")
            }
        }
    }

    private fun updateStatus(status: String) {
        runOnUiThread { statusTextView.text = status }
    }

    private fun bindPasteModeSelection() {
        setPasteModeSelection(selectedPasteMode)
        pasteModeRadioGroup.setOnCheckedChangeListener { _, checkedId ->
            selectedPasteMode = when (checkedId) {
                R.id.pasteModeCtrlShiftVRadioButton -> PASTE_MODE_CTRL_SHIFT_V
                R.id.pasteModeShiftInsertRadioButton -> PASTE_MODE_SHIFT_INSERT
                else -> PASTE_MODE_CTRL_V
            }
            getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putString(PREF_PASTE_MODE, selectedPasteMode)
                .apply()
            if (isConnected && isAuthed) {
                updateStatus("Paste mode updated: $selectedPasteMode")
            }
        }
    }

    private fun setPasteModeSelection(mode: String) {
        val radioButtonId = when (mode) {
            PASTE_MODE_CTRL_SHIFT_V -> R.id.pasteModeCtrlShiftVRadioButton
            PASTE_MODE_SHIFT_INSERT -> R.id.pasteModeShiftInsertRadioButton
            else -> R.id.pasteModeCtrlVRadioButton
        }
        if (pasteModeRadioGroup.checkedRadioButtonId != radioButtonId) {
            pasteModeRadioGroup.check(radioButtonId)
        }
    }

    private inner class SocketListener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            runOnUiThread {
                isConnected = true
                connectButton.text = getString(R.string.disconnect)
                updateStatus("Socket opened")
                sendHelloAndAuth()
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            runOnUiThread { handleMessage(text) }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            runOnUiThread {
                uiHandler.removeCallbacks(heartbeatRunnable)
                onDisconnected("Socket closed: $reason")
                scheduleReconnect()
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            runOnUiThread {
                uiHandler.removeCallbacks(heartbeatRunnable)
                onDisconnected("Socket error: ${t.message ?: "unknown"}")
                scheduleReconnect()
            }
        }
    }
}
