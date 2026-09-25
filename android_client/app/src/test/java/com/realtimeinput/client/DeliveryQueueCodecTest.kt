package com.realtimeinput.client

import org.junit.Assert.assertEquals
import org.junit.Test

class DeliveryQueueCodecTest {
    @Test
    fun roundTripPreservesUnicodeAndOrder() {
        val original = listOf(
            DeliveryOp("op-1", "第一段中文。"),
            DeliveryOp("op-2", "second append 123"),
            DeliveryOp("op-3", "包含 标点、空格。"),
        )
        assertEquals(original, DeliveryQueueCodec.decode(DeliveryQueueCodec.encode(original)))
    }

    @Test
    fun malformedRowsAreIgnoredWithoutDroppingValidRows() {
        val valid = DeliveryQueueCodec.encode(listOf(DeliveryOp("good", "保留")))
        assertEquals(listOf(DeliveryOp("good", "保留")), DeliveryQueueCodec.decode("broken\n$valid\n.bad"))
    }
}
