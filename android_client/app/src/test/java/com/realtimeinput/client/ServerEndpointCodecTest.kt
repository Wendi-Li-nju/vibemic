package com.realtimeinput.client

import org.junit.Assert.assertEquals
import org.junit.Test

class ServerEndpointCodecTest {
    @Test
    fun roundTripPreservesEndpoints() {
        val endpoints = listOf(
            ServerEndpoint("114.212.82.206", 8765, "known"),
            ServerEndpoint("192.168.1.166", 8765, "mdns"),
        )
        assertEquals(endpoints, ServerEndpointCodec.decode(ServerEndpointCodec.encode(endpoints)))
    }

    @Test
    fun duplicateEndpointsAreCollapsed() {
        val encoded = ServerEndpointCodec.encode(
            listOf(
                ServerEndpoint("192.168.1.166", 8765, "known"),
                ServerEndpoint("192.168.1.166", 8765, "mdns"),
            )
        )
        assertEquals(1, ServerEndpointCodec.decode(encoded).size)
    }

    @Test
    fun discoveredCosecRouteWinsOverNjuBootstrapInAutoMode() {
        val candidates = ServerEndpointSelector.build(
            autoSelect = true,
            discovered = listOf(ServerEndpoint("192.168.1.180", 8765, "mdns")),
            last = emptyList(),
            bootstrap = ServerEndpoint("114.212.82.206", 8765, "bootstrap"),
            known = emptyList(),
            defaults = listOf(ServerEndpoint("192.168.1.166", 8765, "default")),
        )
        assertEquals("192.168.1.180", candidates.first().host)
    }

    @Test
    fun njuFallbackRemainsAvailableWhenBootstrapIsCosec() {
        val candidates = ServerEndpointSelector.build(
            autoSelect = true,
            discovered = emptyList(),
            last = emptyList(),
            bootstrap = ServerEndpoint("192.168.1.166", 8765, "bootstrap"),
            known = emptyList(),
            defaults = listOf(ServerEndpoint("114.212.82.206", 8765, "default")),
        )
        assertEquals(listOf("192.168.1.166", "114.212.82.206"), candidates.map { it.host })
    }

    @Test
    fun lateCosecDiscoveryCanReplaceActiveNjuRoute() {
        val current = ServerEndpoint("114.212.82.206", 8765, "bootstrap")
        val discovered = ServerEndpoint("192.168.1.180", 8765, "mdns")
        assertEquals(
            true,
            ServerEndpointSelector.shouldSwitchToDiscovered(
                current = current,
                discovered = discovered,
                connectionActive = true,
            ),
        )
        assertEquals(
            false,
            ServerEndpointSelector.shouldSwitchToDiscovered(
                current = discovered,
                discovered = current,
                connectionActive = true,
            ),
        )
    }

    @Test
    fun manualModeUsesOnlyBootstrap() {
        val bootstrap = ServerEndpoint("114.212.82.206", 8765, "bootstrap")
        val candidates = ServerEndpointSelector.build(
            autoSelect = false,
            discovered = listOf(ServerEndpoint("192.168.1.180", 8765, "mdns")),
            last = emptyList(),
            bootstrap = bootstrap,
            known = emptyList(),
            defaults = emptyList(),
        )
        assertEquals(listOf(bootstrap), candidates)
    }

    @Test
    fun labelsKnownNetworks() {
        assertEquals("NJU", ServerEndpoint("114.212.82.206", 8765).networkLabel())
        assertEquals("Cosec", ServerEndpoint("192.168.1.166", 8765).networkLabel())
    }
}
