package com.realtimeinput.client

import java.nio.charset.StandardCharsets
import java.util.Base64

data class ServerEndpoint(
    val host: String,
    val port: Int,
    val source: String = "known",
) {
    val key: String get() = "$host:$port"

    fun networkLabel(): String = when {
        host.startsWith("114.212.") -> "NJU"
        host.startsWith("192.168.") -> "Cosec"
        host.startsWith("100.") -> "Tailscale"
        source == "mdns" -> "LAN"
        else -> "Direct"
    }

    fun webSocketUrl(): String {
        val urlHost = if (host.contains(':') && !host.startsWith("[")) "[$host]" else host
        return "ws://$urlHost:$port/ws"
    }
}

object ServerEndpointSelector {
    fun build(
        autoSelect: Boolean,
        discovered: List<ServerEndpoint>,
        last: List<ServerEndpoint>,
        bootstrap: ServerEndpoint,
        known: List<ServerEndpoint>,
        defaults: List<ServerEndpoint>,
    ): List<ServerEndpoint> {
        if (!autoSelect) return listOf(bootstrap)
        return (discovered + last + listOf(bootstrap) + known + defaults)
            .distinctBy { it.key }
    }

    fun shouldSwitchToDiscovered(
        current: ServerEndpoint?,
        discovered: ServerEndpoint,
        connectionActive: Boolean,
    ): Boolean {
        return connectionActive &&
            current != null &&
            discovered.networkLabel() == "Cosec" &&
            current.networkLabel() != "Cosec" &&
            current.key != discovered.key
    }
}

object ServerEndpointCodec {
    private val encoder = Base64.getUrlEncoder().withoutPadding()
    private val decoder = Base64.getUrlDecoder()

    fun encode(endpoints: Collection<ServerEndpoint>): String = endpoints
        .distinctBy { it.key }
        .joinToString("\n") { endpoint ->
            listOf(
                encodePart(endpoint.host),
                endpoint.port.toString(),
                encodePart(endpoint.source),
            ).joinToString(".")
        }

    fun decode(raw: String): List<ServerEndpoint> {
        if (raw.isBlank()) return emptyList()
        return raw.lineSequence().mapNotNull { line ->
            val parts = line.split('.')
            if (parts.size != 3) return@mapNotNull null
            runCatching {
                val host = decodePart(parts[0])
                val port = parts[1].toInt()
                val source = decodePart(parts[2])
                if (host.isBlank() || port !in 1..65535) null
                else ServerEndpoint(host, port, source)
            }.getOrNull()
        }.filterNotNull().toList()
    }

    private fun encodePart(value: String): String =
        encoder.encodeToString(value.toByteArray(StandardCharsets.UTF_8))

    private fun decodePart(value: String): String =
        String(decoder.decode(value), StandardCharsets.UTF_8)
}
