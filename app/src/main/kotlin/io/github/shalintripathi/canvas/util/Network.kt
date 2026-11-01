package io.github.shalintripathi.canvas.util

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.SocketException

object Network {
    /** The TV's address on the home network, preferring Wi-Fi, then Ethernet. */
    fun localIpv4(): String? = try {
        NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
            .filter { it.isUp && !it.isLoopback }
            .sortedBy { if (it.name.startsWith("wlan")) 0 else if (it.name.startsWith("eth")) 1 else 2 }
            .flatMap { it.inetAddresses.toList() }
            .firstOrNull { it is Inet4Address && it.isSiteLocalAddress }
            ?.hostAddress
    } catch (e: SocketException) {
        null
    }

    /** "Ethernet", the Wi-Fi name, "Wi-Fi" when the name is hidden from us, or "Offline". */
    @Suppress("DEPRECATION")
    fun name(context: Context): String {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return "Network"
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return "Offline"
        if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return "Ethernet"
        if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return "Online"
        val ssid = try {
            (context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager).connectionInfo?.ssid
        } catch (e: SecurityException) {
            null
        }
        return ssid?.removeSurrounding("\"")?.takeIf { it.isNotBlank() && it != "<unknown ssid>" } ?: "Wi-Fi"
    }
}
