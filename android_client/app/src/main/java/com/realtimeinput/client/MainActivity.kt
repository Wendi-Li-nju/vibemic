package com.realtimeinput.client

import android.app.AlertDialog
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
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {
    companion object {
        private const val INPUT_SEND_DEBOUNCE_MS = 25L
        private const val COMPOSING_RETRY_DELAY_MS = 40L
        private const val APPEND_ONLY_WARNING = "Only append-at-cursor sync is supported"
        private const val PREFS_NAME = "rtcs_prefs"
        private const val PREF_PASTE_MODE = "paste_mode"
        private const val PREF_HOST = "connection_host"
        private const val PREF_PORT = "connection_port"
        private const val DEFAULT_HOST = "114.212.82.206"
        private const val DEFAULT_PORT = "8765"
        private const val PASTE_MODE_CTRL_V = "ctrl_v"
        private const val PASTE_MODE_CTRL_SHIFT_V = "ctrl_shift_v"
        private const val PASTE_MODE_SHIFT_INSERT = "shift_insert"
    }

    private lateinit var endpointTextView: TextView
    private lateinit var connectionSettingsButton: Button
    private lateinit var clearReconnectButton: Button
    private lateinit var connectButton: Button
    private lateinit var statusTextView: TextView
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
    private var selectedPasteMode: String = PASTE_MODE_CTRL_V
    private var lastInputSnapshot: String = ""
    private var isProgrammaticInputChange: Boolean = false
    private val pendingAppends: ArrayDeque<String> = ArrayDeque()
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
            webSocket?.send(ping.toString())
            uiHandler.postDelayed(this, heartbeatIntervalMs)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        endpointTextView = findViewById(R.id.endpointTextView)
        connectionSettingsButton = findViewById(R.id.connectionSettingsButton)
        clearReconnectButton = findViewById(R.id.clearReconnectButton)
        connectButton = findViewById(R.id.connectButton)
        statusTextView = findViewById(R.id.statusTextView)
        inputEditText = findViewById(R.id.inputEditText)
        pasteModeRadioGroup = findViewById(R.id.pasteModeRadioGroup)

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        selectedPasteMode = prefs
            .getString(PREF_PASTE_MODE, PASTE_MODE_CTRL_V)
            ?.takeIf { it in setOf(PASTE_MODE_CTRL_V, PASTE_MODE_CTRL_SHIFT_V, PASTE_MODE_SHIFT_INSERT) }
            ?: PASTE_MODE_CTRL_V
        bindPasteModeSelection()
        updateEndpointSummary()

        connectionSettingsButton.setOnClickListener {
            showConnectionSettingsDialog()
        }

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
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val host = prefs.getString(PREF_HOST, DEFAULT_HOST)?.trim().orEmpty().ifEmpty { DEFAULT_HOST }
        val port = prefs.getString(PREF_PORT, DEFAULT_PORT)?.trim().orEmpty().ifEmpty { DEFAULT_PORT }
        if (host.isEmpty()) {
            statusTextView.text = "Host IP required"
            return
        }
        if (!isValidPort(port)) {
            statusTextView.text = "Valid port required"
            return
        }
        val url = "ws://$host:$port/ws"
        updateStatus("Connecting to $host:$port")
        val req = Request.Builder().url(url).build()
        webSocket = okHttpClient.newWebSocket(req, SocketListener())
    }

    private fun showConnectionSettingsDialog() {
        val dialogView = layoutInflater.inflate(R.layout.dialog_connection_settings, null)
        val hostEditText = dialogView.findViewById<EditText>(R.id.settingsHostEditText)
        val portEditText = dialogView.findViewById<EditText>(R.id.settingsPortEditText)
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

        hostEditText.setText(prefs.getString(PREF_HOST, DEFAULT_HOST) ?: DEFAULT_HOST)
        portEditText.setText(prefs.getString(PREF_PORT, DEFAULT_PORT) ?: DEFAULT_PORT)

        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.connection_settings)
            .setView(dialogView)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .create()

        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val host = hostEditText.text.toString().trim()
                val port = portEditText.text.toString().trim()
                when {
                    host.isEmpty() -> hostEditText.error = getString(R.string.host_required)
                    !isValidPort(port) -> portEditText.error = getString(R.string.valid_port_required)
                    else -> {
                        prefs.edit()
                            .putString(PREF_HOST, host)
                            .putString(PREF_PORT, port)
                            .apply()
                        updateEndpointSummary()
                        updateStatus(
                            if (isConnected) "Settings saved; reconnect to apply"
                            else "Connection settings saved"
                        )
                        dialog.dismiss()
                    }
                }
            }
        }
        dialog.show()
    }

    private fun updateEndpointSummary() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val host = prefs.getString(PREF_HOST, DEFAULT_HOST)?.trim().orEmpty().ifEmpty { DEFAULT_HOST }
        val port = prefs.getString(PREF_PORT, DEFAULT_PORT)?.trim().orEmpty().ifEmpty { DEFAULT_PORT }
        endpointTextView.text = "$host:$port"
    }

    private fun isValidPort(port: String): Boolean {
        val number = port.toIntOrNull() ?: return false
        return number in 1..65535
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
        pendingAppends.clear()
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
        lastInputSnapshot = inputEditText.text?.toString().orEmpty()
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
            "hello_ok" -> updateStatus("Connected, authorizing...")
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
                    updateStatus("Connected")
                    uiHandler.removeCallbacks(heartbeatRunnable)
                    uiHandler.postDelayed(heartbeatRunnable, heartbeatIntervalMs)
                    flushPendingAppends()
                } else {
                    updateStatus("Authorization failed")
                }
            }
            "ack" -> {
                val ok = obj.optBoolean("ok", false)
                val reason = obj.optString("reason")
                if (!ok) {
                    updateStatus(
                        if (reason.isNotEmpty()) "Send failed: $reason" else "Send failed"
                    )
                }
            }
            "pong" -> Unit
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
                updateStatus("Connected, signing in...")
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
                onDisconnected(if (reason.isNotEmpty()) "Disconnected: $reason" else "Disconnected")
                scheduleReconnect()
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            runOnUiThread {
                uiHandler.removeCallbacks(heartbeatRunnable)
                onDisconnected("Connection error: ${t.message ?: "unknown"}")
                scheduleReconnect()
            }
        }
    }
}
