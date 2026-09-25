package com.realtimeinput.client

import android.app.AlertDialog
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.inputmethod.BaseInputConnection
import android.widget.Button
import android.widget.CheckBox
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
import java.net.Inet4Address
import java.util.ArrayDeque
import java.util.LinkedHashMap
import java.util.UUID
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {
    companion object {
        private const val INPUT_SEND_DEBOUNCE_MS = 25L
        private const val COMPOSING_RETRY_DELAY_MS = 40L
        private const val DELIVERY_ACK_TIMEOUT_MS = 10000L
        private const val APPEND_ONLY_WARNING = "Only append-at-cursor sync is supported"
        private const val PREFS_NAME = "rtcs_prefs"
        private const val PREF_PASTE_MODE = "paste_mode"
        private const val PREF_HOST = "connection_host"
        private const val PREF_PORT = "connection_port"
        private const val PREF_LOCAL_DRAFT = "local_input_draft"
        private const val PREF_SYNC_BASELINE = "local_sync_baseline"
        private const val PREF_DELIVERY_QUEUE = "delivery_queue"
        private const val PREF_CLIENT_ID = "client_id"
        private const val PREF_AUTO_SELECT = "auto_select_network"
        private const val PREF_SERVER_ID = "server_id"
        private const val PREF_LAST_ENDPOINT = "last_endpoint"
        private const val PREF_KNOWN_ENDPOINTS = "known_endpoints"
        private const val DEFAULT_HOST = "114.212.82.206"
        private const val DEFAULT_COSEC_HOST = "192.168.1.166"
        private const val DEFAULT_PORT = "8765"
        private const val MDNS_SERVICE_TYPE = "_vibemic._tcp."
        private const val MDNS_DISCOVERY_GRACE_MS = 1200L
        private const val CONNECT_TIMEOUT_MS = 2500L
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

    private lateinit var clientId: String
    private val uiHandler = Handler(Looper.getMainLooper())
    private val okHttpClient = OkHttpClient.Builder()
        .connectTimeout(CONNECT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var isConnected = false
    private var isConnecting = false
    private var isAuthed = false
    private var reconnectAttempt = 0
    private var shouldReconnect = false
    private var socketGeneration = 0L
    private val connectionCandidates: ArrayDeque<ServerEndpoint> = ArrayDeque()
    private val discoveredEndpoints: LinkedHashMap<String, ServerEndpoint> = LinkedHashMap()
    private var activeEndpoint: ServerEndpoint? = null
    private var connectedServerId: String = ""
    private var discoveryStarted = false
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private var autoDiscoveryGraceUsed = false

    private var sessionId: String = ""
    private var token: String = ""
    private var heartbeatIntervalMs: Long = 5000L
    private var localSeq: Int = 0
    private var selectedPasteMode: String = PASTE_MODE_CTRL_V
    private var lastInputSnapshot: String = ""
    private var isProgrammaticInputChange: Boolean = false
    private var reauthInProgress: Boolean = false
    private val deliveryQueue: ArrayDeque<DeliveryOp> = ArrayDeque()
    private var inflightOp: DeliveryOp? = null
    private var inflightSeq: Int = 0
    private val processInputRunnable = Runnable { maybeProcessInputText() }
    private val deliveryAckTimeoutRunnable = Runnable {
        if (inflightOp != null) {
            inflightOp = null
            inflightSeq = 0
            persistDeliveryState(sync = false)
            restartConnection("Delivery confirmation timed out; text kept for retry")
        }
    }

    private val reconnectRunnable = object : Runnable {
        override fun run() {
            if (!shouldReconnect || isConnected || isConnecting) return
            connectInternal()
        }
    }

    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            if (!isConnected || !isAuthed) return
            if (!sendHeartbeatNow()) {
                restartConnection("Connection stalled; text kept for retry")
                return
            }
            if (isConnected && isAuthed) {
                uiHandler.postDelayed(this, heartbeatIntervalMs)
            }
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
        clientId = prefs.getString(PREF_CLIENT_ID, null)?.takeIf { it.isNotBlank() }
            ?: ("android-" + UUID.randomUUID().toString()).also {
                prefs.edit().putString(PREF_CLIENT_ID, it).commit()
            }
        selectedPasteMode = prefs
            .getString(PREF_PASTE_MODE, PASTE_MODE_CTRL_V)
            ?.takeIf { it in setOf(PASTE_MODE_CTRL_V, PASTE_MODE_CTRL_SHIFT_V, PASTE_MODE_SHIFT_INSERT) }
            ?: PASTE_MODE_CTRL_V
        bindPasteModeSelection()
        updateEndpointSummary()
        restorePersistentInputState()
        if (prefs.getBoolean(PREF_AUTO_SELECT, true)) {
            startNetworkDiscovery()
        }

        connectionSettingsButton.setOnClickListener {
            showConnectionSettingsDialog()
        }

        connectButton.setOnClickListener {
            if (isConnected) {
                disconnectManual()
            } else {
                shouldReconnect = true
                connectionCandidates.clear()
                autoDiscoveryGraceUsed = false
                connectInternal()
            }
        }

        clearReconnectButton.setOnClickListener {
            requestClearLocalInput()
        }

        inputEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}

            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                persistLocalDraft(s?.toString().orEmpty())
                if (isProgrammaticInputChange) {
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

    override fun onResume() {
        super.onResume()
        if (isConnected && isAuthed) {
            if (!sendHeartbeatNow()) {
                restartConnection("Connection stalled; reconnecting...")
            }
        } else if (shouldReconnect && !isConnected && !isConnecting) {
            scheduleReconnect()
        }
    }

    override fun onPause() {
        persistDeliveryState(sync = true)
        super.onPause()
    }

    override fun onDestroy() {
        persistDeliveryState(sync = true)
        shouldReconnect = false
        uiHandler.removeCallbacks(heartbeatRunnable)
        uiHandler.removeCallbacks(reconnectRunnable)
        uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
        socketGeneration += 1
        val socket = webSocket
        webSocket = null
        isConnecting = false
        socket?.close(1000, "Activity destroy")
        stopNetworkDiscovery()
        okHttpClient.dispatcher.executorService.shutdown()
        super.onDestroy()
    }

    private fun connectInternal() {
        if (isConnected || isConnecting) return
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        if (
            prefs.getBoolean(PREF_AUTO_SELECT, true) &&
            !autoDiscoveryGraceUsed &&
            discoveredEndpoints.isEmpty() &&
            discoveryListener != null
        ) {
            autoDiscoveryGraceUsed = true
            updateStatus("Looking for a direct 4090 route...")
            uiHandler.postDelayed({ connectInternal() }, MDNS_DISCOVERY_GRACE_MS)
            return
        }
        if (connectionCandidates.isEmpty()) {
            connectionCandidates.addAll(buildConnectionCandidates())
        }
        val endpoint = connectionCandidates.pollFirst()
        if (endpoint == null) {
            scheduleReconnect()
            return
        }

        activeEndpoint = endpoint
        updateEndpointSummary()
        updateStatus("Connecting via ${endpoint.networkLabel()} · ${endpoint.host}:${endpoint.port}")
        isConnecting = true
        val generation = ++socketGeneration
        val req = Request.Builder().url(endpoint.webSocketUrl()).build()
        webSocket = clientForEndpoint(endpoint).newWebSocket(req, SocketListener(generation))
    }

    @Suppress("DEPRECATION")
    private fun clientForEndpoint(endpoint: ServerEndpoint): OkHttpClient {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        if (!prefs.getBoolean(PREF_AUTO_SELECT, true)) return okHttpClient
        if (endpoint.networkLabel() == "Tailscale") return okHttpClient
        val connectivity = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val wifiNetwork = connectivity.allNetworks.firstOrNull { network ->
            val caps = connectivity.getNetworkCapabilities(network)
            caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        } ?: return okHttpClient
        return okHttpClient.newBuilder()
            .socketFactory(wifiNetwork.socketFactory)
            .build()
    }

    private fun buildConnectionCandidates(): List<ServerEndpoint> {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val bootstrapHost = prefs.getString(PREF_HOST, DEFAULT_HOST)?.trim().orEmpty().ifEmpty { DEFAULT_HOST }
        val bootstrapPort = prefs.getString(PREF_PORT, DEFAULT_PORT)?.toIntOrNull() ?: DEFAULT_PORT.toInt()
        val bootstrap = ServerEndpoint(bootstrapHost, bootstrapPort, "bootstrap")
        if (!prefs.getBoolean(PREF_AUTO_SELECT, true)) {
            return listOf(bootstrap)
        }

        val last = ServerEndpointCodec.decode(prefs.getString(PREF_LAST_ENDPOINT, "").orEmpty())
        val known = ServerEndpointCodec.decode(prefs.getString(PREF_KNOWN_ENDPOINTS, "").orEmpty())
        val defaults = listOf(
            ServerEndpoint(DEFAULT_HOST, DEFAULT_PORT.toInt(), "default"),
            ServerEndpoint(DEFAULT_COSEC_HOST, DEFAULT_PORT.toInt(), "default"),
        )
        return ServerEndpointSelector.build(
            autoSelect = true,
            discovered = discoveredEndpoints.values.toList(),
            last = last,
            bootstrap = bootstrap,
            known = known,
            defaults = defaults,
        )
    }

    private fun enqueueDiscoveredEndpoint(endpoint: ServerEndpoint) {
        val current = activeEndpoint
        if (current?.key == endpoint.key) return
        if (connectionCandidates.none { it.key == endpoint.key }) {
            connectionCandidates.addFirst(endpoint)
        }

        val shouldPreferDirectCosec = ServerEndpointSelector.shouldSwitchToDiscovered(
            current = current,
            discovered = endpoint,
            connectionActive = isConnected || isConnecting,
        )

        if (shouldPreferDirectCosec && current != null) {
            if (connectionCandidates.none { it.key == current.key }) {
                connectionCandidates.addLast(current.copy(source = "fallback"))
            }
            uiHandler.removeCallbacks(reconnectRunnable)
            restartConnection("Direct Cosec route discovered; switching safely...")
            return
        }

        if (shouldReconnect && !isConnected && !isConnecting) {
            connectInternal()
        }
    }

    private fun tryNextCandidateOrSchedule(reason: String) {
        onDisconnected(reason)
        if (!shouldReconnect) return
        if (connectionCandidates.isNotEmpty()) {
            uiHandler.postDelayed({ connectInternal() }, 120L)
        } else {
            autoDiscoveryGraceUsed = false
            scheduleReconnect()
        }
    }

    private fun showConnectionSettingsDialog() {
        val dialogView = layoutInflater.inflate(R.layout.dialog_connection_settings, null)
        val hostEditText = dialogView.findViewById<EditText>(R.id.settingsHostEditText)
        val portEditText = dialogView.findViewById<EditText>(R.id.settingsPortEditText)
        val autoSelectCheckBox = dialogView.findViewById<CheckBox>(R.id.settingsAutoSelectCheckBox)
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

        hostEditText.setText(prefs.getString(PREF_HOST, DEFAULT_HOST) ?: DEFAULT_HOST)
        portEditText.setText(prefs.getString(PREF_PORT, DEFAULT_PORT) ?: DEFAULT_PORT)
        autoSelectCheckBox.isChecked = prefs.getBoolean(PREF_AUTO_SELECT, true)

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
                        val oldHost = prefs.getString(PREF_HOST, DEFAULT_HOST).orEmpty()
                        val oldPort = prefs.getString(PREF_PORT, DEFAULT_PORT).orEmpty()
                        val bootstrapChanged = oldHost != host || oldPort != port
                        val editor = prefs.edit()
                            .putString(PREF_HOST, host)
                            .putString(PREF_PORT, port)
                            .putBoolean(PREF_AUTO_SELECT, autoSelectCheckBox.isChecked)
                        if (bootstrapChanged) {
                            editor.remove(PREF_SERVER_ID)
                                .remove(PREF_LAST_ENDPOINT)
                                .remove(PREF_KNOWN_ENDPOINTS)
                            connectedServerId = ""
                            activeEndpoint = null
                        }
                        editor.apply()
                        connectionCandidates.clear()
                        autoDiscoveryGraceUsed = false
                        if (autoSelectCheckBox.isChecked) {
                            startNetworkDiscovery()
                        } else {
                            stopNetworkDiscovery()
                        }
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
        val endpoint = activeEndpoint ?: ServerEndpointCodec
            .decode(prefs.getString(PREF_LAST_ENDPOINT, "").orEmpty())
            .firstOrNull()
        if (endpoint != null && prefs.getBoolean(PREF_AUTO_SELECT, true)) {
            endpointTextView.text = "4090 · ${endpoint.networkLabel()} · ${endpoint.host}:${endpoint.port}"
            return
        }
        val host = prefs.getString(PREF_HOST, DEFAULT_HOST)?.trim().orEmpty().ifEmpty { DEFAULT_HOST }
        val port = prefs.getString(PREF_PORT, DEFAULT_PORT)?.trim().orEmpty().ifEmpty { DEFAULT_PORT }
        endpointTextView.text = if (prefs.getBoolean(PREF_AUTO_SELECT, true)) {
            "4090 · Auto · $host:$port"
        } else {
            "$host:$port"
        }
    }

    private fun rememberSuccessfulEndpoint() {
        val endpoint = activeEndpoint ?: return
        if (connectedServerId.isEmpty()) return
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val known = ServerEndpointCodec
            .decode(prefs.getString(PREF_KNOWN_ENDPOINTS, "").orEmpty())
            .plus(endpoint.copy(source = "verified"))
            .distinctBy { it.key }
            .takeLast(12)
        prefs.edit()
            .putString(PREF_SERVER_ID, connectedServerId)
            .putString(PREF_LAST_ENDPOINT, ServerEndpointCodec.encode(listOf(endpoint.copy(source = "last"))))
            .putString(PREF_KNOWN_ENDPOINTS, ServerEndpointCodec.encode(known))
            .commit()
        updateEndpointSummary()
    }

    private fun startNetworkDiscovery() {
        if (discoveryStarted || discoveryListener != null || !getSharedPreferences(PREFS_NAME, MODE_PRIVATE).getBoolean(PREF_AUTO_SELECT, true)) return
        val wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        if (multicastLock == null) {
            multicastLock = wifiManager?.createMulticastLock("VibeMic-mDNS")?.apply {
                setReferenceCounted(false)
                runCatching { acquire() }
            }
        } else if (multicastLock?.isHeld == false) {
            runCatching { multicastLock?.acquire() }
        }

        val nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {
                discoveryStarted = true
            }

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (!serviceInfo.serviceType.startsWith("_vibemic._tcp")) return
                if (!serviceInfo.serviceName.startsWith("VibeMic 4090")) return
                @Suppress("DEPRECATION")
                nsdManager.resolveService(serviceInfo, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = Unit

                    override fun onServiceResolved(resolved: NsdServiceInfo) {
                        val address = resolved.host
                        val host = if (address is Inet4Address) address.hostAddress else null
                        val port = resolved.port
                        if (host.isNullOrBlank() || port !in 1..65535) return
                        val endpoint = ServerEndpoint(host, port, "mdns")
                        runOnUiThread {
                            discoveredEndpoints[resolved.serviceName] = endpoint
                            enqueueDiscoveredEndpoint(endpoint)
                        }
                    }
                })
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) {
                runOnUiThread { discoveredEndpoints.remove(serviceInfo.serviceName) }
            }

            override fun onDiscoveryStopped(serviceType: String) {
                discoveryStarted = false
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                discoveryStarted = false
                discoveryListener = null
                multicastLock?.let { lock -> if (lock.isHeld) runCatching { lock.release() } }
                multicastLock = null
                runCatching { nsdManager.stopServiceDiscovery(this) }
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                discoveryStarted = false
            }
        }
        discoveryListener = listener
        runCatching {
            nsdManager.discoverServices(MDNS_SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
        }.onFailure {
            discoveryStarted = false
            discoveryListener = null
        }
    }

    private fun stopNetworkDiscovery() {
        val listener = discoveryListener
        if (listener != null) {
            val nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
            runCatching { nsdManager.stopServiceDiscovery(listener) }
        }
        discoveryListener = null
        discoveryStarted = false
        discoveredEndpoints.clear()
        multicastLock?.let { lock -> if (lock.isHeld) runCatching { lock.release() } }
        multicastLock = null
    }

    private fun isValidPort(port: String): Boolean {
        val number = port.toIntOrNull() ?: return false
        return number in 1..65535
    }

    private fun disconnectManual() {
        shouldReconnect = false
        uiHandler.removeCallbacks(heartbeatRunnable)
        uiHandler.removeCallbacks(reconnectRunnable)
        uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
        socketGeneration += 1
        val socket = webSocket
        webSocket = null
        isConnecting = false
        connectionCandidates.clear()
        autoDiscoveryGraceUsed = false
        activeEndpoint = null
        socket?.close(1000, "Manual disconnect")
        onDisconnected("Disconnected")
        updateEndpointSummary()
    }

    private fun requestClearLocalInput() {
        val current = inputEditText.text?.toString().orEmpty()
        if (current.isEmpty()) {
            clearLocalInput()
            return
        }
        val pendingNote = if (deliveryQueue.isNotEmpty()) {
            " Pending text already queued for desktop delivery will be kept."
        } else {
            ""
        }
        AlertDialog.Builder(this)
            .setTitle("Clear local draft?")
            .setMessage("This removes the text shown on this phone.$pendingNote")
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton("Clear") { _, _ -> clearLocalInput() }
            .show()
    }

    private fun clearLocalInput() {
        uiHandler.removeCallbacks(processInputRunnable)
        isProgrammaticInputChange = true
        inputEditText.setText("")
        isProgrammaticInputChange = false
        lastInputSnapshot = ""
        persistDeliveryState(sync = true)
        updateStatus(
            if (deliveryQueue.isNotEmpty()) "Local draft cleared; pending delivery kept"
            else "Local input cleared"
        )
        if (isConnected && isAuthed) {
            sendHeartbeatNow()
        }
    }

    private fun maybeProcessInputText() {
        val editable = inputEditText.text
        if (editable != null && BaseInputConnection.getComposingSpanStart(editable) != -1) {
            persistLocalDraft(editable.toString())
            scheduleProcessInputText(COMPOSING_RETRY_DELAY_MS)
            return
        }

        val rawCurrent = editable?.toString().orEmpty()
        persistLocalDraft(rawCurrent)
        if (rawCurrent == lastInputSnapshot) return

        val current = InputSanitizer.normalizeControlChars(rawCurrent)
        if (current != rawCurrent) {
            replaceLocalInputWithoutChangingSyncBaseline(current)
            persistLocalDraft(current)
            updateStatus("Line break normalized; draft preserved")
        }

        if (current == lastInputSnapshot) return
        if (!current.startsWith(lastInputSnapshot)) {
            lastInputSnapshot = current
            persistDeliveryState(sync = true)
            updateStatus("$APPEND_ONLY_WARNING; edit kept locally, future appends will sync")
            return
        }

        val suffix = current.substring(lastInputSnapshot.length)
        if (suffix.isNotEmpty()) {
            deliveryQueue.addLast(DeliveryOp(UUID.randomUUID().toString(), suffix))
        }
        lastInputSnapshot = current
        persistDeliveryState(sync = true)
        drainDeliveryQueue()
    }

    private fun replaceLocalInputWithoutChangingSyncBaseline(text: String) {
        isProgrammaticInputChange = true
        inputEditText.setText(text)
        inputEditText.setSelection(text.length)
        isProgrammaticInputChange = false
    }

    private fun persistLocalDraft(text: String) {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putString(PREF_LOCAL_DRAFT, text)
            .commit()
    }

    private fun persistDeliveryState(sync: Boolean) {
        val editor = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putString(PREF_LOCAL_DRAFT, inputEditText.text?.toString().orEmpty())
            .putString(PREF_SYNC_BASELINE, lastInputSnapshot)
            .putString(PREF_DELIVERY_QUEUE, DeliveryQueueCodec.encode(deliveryQueue))
        if (sync) {
            editor.commit()
        } else {
            editor.apply()
        }
    }

    private fun restorePersistentInputState() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        deliveryQueue.clear()
        deliveryQueue.addAll(
            DeliveryQueueCodec.decode(prefs.getString(PREF_DELIVERY_QUEUE, "").orEmpty())
        )

        val rawDraft = prefs.getString(PREF_LOCAL_DRAFT, "").orEmpty()
        val draft = InputSanitizer.normalizeControlChars(rawDraft)
        val savedBaseline = prefs.getString(PREF_SYNC_BASELINE, "").orEmpty()
        if (draft.isNotEmpty()) {
            replaceLocalInputWithoutChangingSyncBaseline(draft)
        }

        if (draft.startsWith(savedBaseline)) {
            val unsentTail = draft.substring(savedBaseline.length)
            if (unsentTail.isNotEmpty()) {
                deliveryQueue.addLast(DeliveryOp(UUID.randomUUID().toString(), unsentTail))
                lastInputSnapshot = draft
                persistDeliveryState(sync = true)
                updateStatus(
                    "Recovered unsent draft and queued it safely (" + deliveryQueue.size + " pending)"
                )
            } else {
                lastInputSnapshot = savedBaseline
                if (draft != rawDraft) {
                    persistDeliveryState(sync = true)
                }
                when {
                    deliveryQueue.isNotEmpty() ->
                        updateStatus("Recovered draft and " + deliveryQueue.size + " pending delivery item(s)")
                    draft.isNotEmpty() ->
                        updateStatus("Recovered local draft")
                }
            }
        } else {
            // Never auto-resend an ambiguous restored draft. Preserve it locally and
            // establish a new safe baseline instead of overwriting user text.
            lastInputSnapshot = draft
            persistDeliveryState(sync = true)
            updateStatus("Recovered draft safely; previous sync state was inconsistent")
        }
    }

    private fun scheduleProcessInputText(delayMs: Long) {
        uiHandler.removeCallbacks(processInputRunnable)
        uiHandler.postDelayed(processInputRunnable, delayMs)
    }

    private fun sendHeartbeatNow(): Boolean {
        if (!isConnected || !isAuthed || sessionId.isEmpty() || token.isEmpty()) return false
        val ts = System.currentTimeMillis()
        val ping = JSONObject()
            .put("type", "ping")
            .put("session_id", sessionId)
            .put("token", token)
            .put("ts", ts)
        return webSocket?.send(ping.toString()) == true
    }

    private fun isSessionRecoveryReason(reason: String): Boolean {
        return reason == "no_active_session" ||
            reason == "invalid_session" ||
            reason == "out_of_order"
    }

    private fun recoverSession() {
        if (!isConnected || reauthInProgress) return
        reauthInProgress = true
        isAuthed = false
        sessionId = ""
        token = ""
        localSeq = 0
        inflightOp = null
        inflightSeq = 0
        uiHandler.removeCallbacks(heartbeatRunnable)
        uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
        persistDeliveryState(sync = false)
        updateStatus("Connection state changed; text kept. Reauthorizing...")
        if (!sendHello()) {
            restartConnection("Connection stalled; reconnecting...")
        }
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect || isConnecting || isConnected) return
        reconnectAttempt += 1
        val backoffMs = minOf(5000L, (500L shl (reconnectAttempt - 1).coerceAtMost(3)))
        updateStatus("Reconnecting in ${backoffMs}ms")
        uiHandler.removeCallbacks(reconnectRunnable)
        uiHandler.postDelayed(reconnectRunnable, backoffMs)
    }

    private fun onDisconnected(reason: String) {
        isConnecting = false
        isConnected = false
        isAuthed = false
        sessionId = ""
        token = ""
        localSeq = 0
        reauthInProgress = false
        connectedServerId = ""
        inflightOp = null
        inflightSeq = 0
        uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
        persistDeliveryState(sync = false)
        connectButton.text = getString(R.string.connect)
        updateStatus(reason)
    }

    private fun restartConnection(reason: String) {
        uiHandler.removeCallbacks(heartbeatRunnable)
        uiHandler.removeCallbacks(reconnectRunnable)
        socketGeneration += 1
        val socket = webSocket
        webSocket = null
        socket?.cancel()
        tryNextCandidateOrSchedule(reason)
    }

    private fun sendHello(): Boolean {
        val socket = webSocket ?: return false
        val hello = JSONObject()
            .put("type", "hello")
            .put("client_id", clientId)
            .put("app_ver", "1.2.0")
        return socket.send(hello.toString())
    }

    private fun sendAuth(): Boolean {
        val socket = webSocket ?: return false
        val auth = JSONObject()
            .put("type", "auth")
            .put("paste_mode", selectedPasteMode)
        return socket.send(auth.toString())
    }

    private fun drainDeliveryQueue() {
        if (!isConnected || !isAuthed || sessionId.isEmpty() || token.isEmpty()) return
        if (inflightOp != null || deliveryQueue.isEmpty()) return

        val op = deliveryQueue.first()
        val seq = localSeq + 1
        val msg = JSONObject()
            .put("type", "text_insert")
            .put("session_id", sessionId)
            .put("token", token)
            .put("seq", seq)
            .put("op_id", op.id)
            .put("text", op.text)
            .put("paste_mode", selectedPasteMode)
            .put("ts", System.currentTimeMillis())

        inflightOp = op
        inflightSeq = seq
        val sent = webSocket?.send(msg.toString()) == true
        if (sent) {
            localSeq = seq
            uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
            uiHandler.postDelayed(deliveryAckTimeoutRunnable, DELIVERY_ACK_TIMEOUT_MS)
            updateStatus(
                if (deliveryQueue.size > 1) "Sending queued text..."
                else "Connected"
            )
        } else {
            inflightOp = null
            inflightSeq = 0
            restartConnection("Connection stalled; text kept for retry")
        }
    }

    private fun handleMessage(text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "hello_ok" -> {
                val serverId = obj.optString("server_id")
                if (serverId.isEmpty()) {
                    restartConnection("Endpoint has no stable server identity; trying another route")
                    return
                }
                val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                val expectedServerId = prefs.getString(PREF_SERVER_ID, "").orEmpty()
                if (expectedServerId.isNotEmpty() && expectedServerId != serverId) {
                    restartConnection("Different VibeMic server found; trying another 4090 route")
                    return
                }
                connectedServerId = serverId
                updateStatus("4090 verified via ${activeEndpoint?.networkLabel() ?: "network"}; authorizing...")
                if (!sendAuth()) {
                    restartConnection("Connection stalled; trying another 4090 route")
                }
            }
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
                    reauthInProgress = false
                    reconnectAttempt = 0
                    rememberSuccessfulEndpoint()
                    connectionCandidates.clear()
                    updateStatus(
                        if (deliveryQueue.isNotEmpty()) "Connected; resuming pending delivery"
                        else "Connected"
                    )
                    uiHandler.removeCallbacks(heartbeatRunnable)
                    uiHandler.postDelayed(heartbeatRunnable, heartbeatIntervalMs)
                    drainDeliveryQueue()
                } else {
                    updateStatus("Authorization failed")
                }
            }
            "ack" -> {
                val seq = obj.optInt("seq", -1)
                val opId = obj.optString("op_id")
                val ok = obj.optBoolean("ok", false)
                val currentInflight = inflightOp
                val matchesInflight = currentInflight != null &&
                    seq == inflightSeq &&
                    opId == currentInflight.id

                if (ok && matchesInflight) {
                    uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
                    if (deliveryQueue.firstOrNull()?.id == currentInflight?.id) {
                        deliveryQueue.removeFirst()
                    }
                    inflightOp = null
                    inflightSeq = 0
                    persistDeliveryState(sync = true)
                    updateStatus(
                        if (deliveryQueue.isNotEmpty()) "Delivered; sending next queued text..."
                        else "Connected"
                    )
                    drainDeliveryQueue()
                } else if (currentInflight != null) {
                    uiHandler.removeCallbacks(deliveryAckTimeoutRunnable)
                    inflightOp = null
                    inflightSeq = 0
                    persistDeliveryState(sync = false)
                    restartConnection(
                        if (ok) "Unexpected delivery confirmation; text kept for safe retry"
                        else "Delivery was not confirmed; text kept for retry"
                    )
                }
            }
            "pong" -> Unit
            "error" -> {
                val reason = obj.optString("reason")
                if (isSessionRecoveryReason(reason) || inflightOp != null) {
                    inflightOp = null
                    inflightSeq = 0
                    persistDeliveryState(sync = false)
                    recoverSession()
                } else {
                    restartConnection("Connection problem; reconnecting safely")
                }
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

    private fun isCurrentSocket(generation: Long, socket: WebSocket): Boolean {
        return generation == socketGeneration && socket === webSocket
    }

    private inner class SocketListener(
        private val generation: Long,
    ) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            runOnUiThread {
                if (!isCurrentSocket(generation, webSocket)) {
                    webSocket.close(1000, "Superseded connection")
                    return@runOnUiThread
                }
                isConnecting = false
                isConnected = true
                connectButton.text = getString(R.string.disconnect)
                updateStatus("Connected, checking 4090 identity...")
                if (!sendHello()) {
                    restartConnection("Connection stalled; trying another 4090 route")
                }
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            runOnUiThread {
                if (isCurrentSocket(generation, webSocket)) {
                    handleMessage(text)
                }
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            runOnUiThread {
                if (!isCurrentSocket(generation, webSocket)) return@runOnUiThread
                this@MainActivity.webSocket = null
                uiHandler.removeCallbacks(heartbeatRunnable)
                tryNextCandidateOrSchedule("Route disconnected; text kept for retry")
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            runOnUiThread {
                if (!isCurrentSocket(generation, webSocket)) return@runOnUiThread
                this@MainActivity.webSocket = null
                uiHandler.removeCallbacks(heartbeatRunnable)
                tryNextCandidateOrSchedule("Route unavailable; trying another 4090 route")
            }
        }
    }
}
