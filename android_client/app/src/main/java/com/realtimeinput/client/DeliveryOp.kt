package com.realtimeinput.client

import java.nio.charset.StandardCharsets
import java.util.Base64

data class DeliveryOp(
    val id: String,
    val text: String,
)

object DeliveryQueueCodec {
    private val encoder = Base64.getUrlEncoder().withoutPadding()
    private val decoder = Base64.getUrlDecoder()

    fun encode(ops: Collection<DeliveryOp>): String {
        return ops.joinToString("\n") { op ->
            encodePart(op.id) + "." + encodePart(op.text)
        }
    }

    fun decode(raw: String): List<DeliveryOp> {
        if (raw.isBlank()) return emptyList()
        return raw.lineSequence().mapNotNull { line ->
            val separator = line.indexOf('.')
            if (separator <= 0 || separator >= line.length - 1) return@mapNotNull null
            runCatching {
                DeliveryOp(
                    id = decodePart(line.substring(0, separator)),
                    text = decodePart(line.substring(separator + 1)),
                )
            }.getOrNull()
        }.filter { it.id.isNotBlank() && it.text.isNotEmpty() }.toList()
    }

    private fun encodePart(value: String): String {
        return encoder.encodeToString(value.toByteArray(StandardCharsets.UTF_8))
    }

    private fun decodePart(value: String): String {
        return String(decoder.decode(value), StandardCharsets.UTF_8)
    }
}
