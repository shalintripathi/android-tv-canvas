package io.github.shalintripathi.canvas.core.http

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.Inet6Address
import java.net.InetAddress
import java.net.NetworkInterface

class LanGuardTest {
    private fun address(literal: String) = InetAddress.getByName(literal) // literals only: no DNS lookup

    /** ::ffff:a.b.c.d kept as a 16-byte IPv6 address (InetAddress.getByName would turn it into IPv4). */
    private fun mapped(a: Int, b: Int, c: Int, d: Int): InetAddress {
        val bytes = ByteArray(16)
        bytes[10] = 0xff.toByte()
        bytes[11] = 0xff.toByte()
        listOf(a, b, c, d).forEachIndexed { i, v -> bytes[12 + i] = v.toByte() }
        return Inet6Address.getByAddress(null, bytes, null as NetworkInterface?)
    }

    @Test
    fun homeNetworkAddressesAreLan() {
        for (literal in listOf(
            "127.0.0.1", "127.8.9.10", "::1",
            "10.0.0.1", "10.255.255.255",
            "172.16.0.1", "172.31.255.254",
            "192.168.0.1", "192.168.0.20",
            "169.254.10.20",
            "100.64.0.1", "100.127.255.254",
            "fe80::1", "febf::1",
            "fc00::1", "fd12:3456:789a::1",
        )) {
            assertTrue(literal, LanGuard.isLanAddress(address(literal)))
        }
    }

    @Test
    fun internetAddressesAreNot() {
        for (literal in listOf(
            "8.8.8.8", "1.1.1.1", "0.0.0.0",
            "172.15.255.255", "172.32.0.1",
            "192.169.0.1", "169.255.0.1",
            "100.63.255.255", "100.128.0.1",
            "11.0.0.1", "126.255.255.255",
            "2001:4860:4860::8888", "2a00:1450::1", "::", "fec0::1", "fe00::1",
        )) {
            assertFalse(literal, LanGuard.isLanAddress(address(literal)))
        }
    }

    @Test
    fun anIpv4MappedAddressIsJudgedAsTheIpv4AddressInside() {
        assertEquals(16, mapped(192, 168, 1, 5).address.size)
        assertTrue(LanGuard.isLanAddress(mapped(192, 168, 1, 5)))
        assertTrue(LanGuard.isLanAddress(mapped(127, 0, 0, 1)))
        assertTrue(LanGuard.isLanAddress(mapped(10, 1, 2, 3)))
        assertFalse(LanGuard.isLanAddress(mapped(8, 8, 8, 8)))
        assertFalse(LanGuard.isLanAddress(mapped(100, 128, 0, 1)))
    }

    @Test
    fun ipLiteralsAndLocalhostAreAllowedHosts() {
        for (host in listOf(
            "192.168.0.20", "192.168.0.20:8765", "10.0.0.2:80", "127.0.0.1:65535",
            "[fe80::1]", "[fe80::1]:8765", "[::1]:8765", "[::ffff:192.168.1.5]:8765",
            "localhost", "localhost:8765", "LocalHost:8765",
        )) {
            assertTrue(host, LanGuard.isAllowedHost(host))
        }
    }

    @Test
    fun hostNamesAndMalformedHostsAreNot() {
        for (host in listOf(
            null, "", " ",
            "evil.example", "evil.example:8765", "tv.local:8765", "192.168.0.20.nip.io", "localhost.evil.example",
            "256.1.1.1", "1.2.3", "1.2.3.4.5", "192.168.0.20:", "192.168.0.20:0", "192.168.0.20:65536",
            "192.168.0.20:abc", "fe80::1", "[fe80::1", "[]", "[evil.example]", "[::1]:8765x", "localhost:",
        )) {
            assertFalse(host.toString(), LanGuard.isAllowedHost(host))
        }
    }
}
