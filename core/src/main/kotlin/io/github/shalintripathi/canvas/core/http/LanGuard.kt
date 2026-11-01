package io.github.shalintripathi.canvas.core.http

import java.net.InetAddress

/**
 * Keeps the phone page to the home network. [isLanAddress] decides which clients may connect at all;
 * [isAllowedHost] refuses requests that name the TV by a host name, which is how a web page on the
 * internet would reach it through DNS rebinding (its own name, re-pointed at the TV's address).
 */
object LanGuard {
    private val IPV4_HOST = Regex("""(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(:\d{1,5})?""")
    private val IPV6_HOST = Regex("""\[([0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*)](:\d{1,5})?""")
    private val LOCALHOST = Regex("""localhost(:\d{1,5})?""", RegexOption.IGNORE_CASE)

    /**
     * Loopback, the private IPv4 ranges (10/8, 172.16/12, 192.168/16), link-local 169.254/16, carrier-grade NAT
     * 100.64/10 (some mesh VPNs), IPv6 link-local fe80::/10 and unique-local fc00::/7. An IPv4-mapped IPv6
     * address (::ffff:a.b.c.d) is judged as the IPv4 address inside it.
     */
    fun isLanAddress(address: InetAddress): Boolean {
        if (address.isLoopbackAddress) return true
        val b = address.address.map { it.toInt() and 0xff }
        return when {
            b.size == 4 -> isLanIpv4(b)
            b.size == 16 && b.subList(0, 10).all { it == 0 } && b[10] == 0xff && b[11] == 0xff -> isLanIpv4(b.subList(12, 16))
            b.size == 16 -> (b[0] == 0xfe && (b[1] and 0xc0) == 0x80) || (b[0] and 0xfe) == 0xfc
            else -> false
        }
    }

    private fun isLanIpv4(b: List<Int>): Boolean = when {
        b[0] == 127 || b[0] == 10 -> true
        b[0] == 172 && b[1] in 16..31 -> true
        b[0] == 192 && b[1] == 168 -> true
        b[0] == 169 && b[1] == 254 -> true
        b[0] == 100 && b[1] in 64..127 -> true
        else -> false
    }

    /** An IPv4 literal, a bracketed IPv6 literal or `localhost`, each with an optional port. No Host header: false. */
    fun isAllowedHost(host: String?): Boolean {
        val value = host?.trim() ?: return false
        IPV4_HOST.matchEntire(value)?.let { match ->
            return (1..4).all { match.groupValues[it].toInt() <= 255 } && validPort(match.groupValues[5])
        }
        IPV6_HOST.matchEntire(value)?.let { return validPort(it.groupValues[2]) }
        return LOCALHOST.matchEntire(value)?.let { validPort(it.groupValues[1]) } ?: false
    }

    private fun validPort(part: String) = part.isEmpty() || part.drop(1).toInt() in 1..65535
}
