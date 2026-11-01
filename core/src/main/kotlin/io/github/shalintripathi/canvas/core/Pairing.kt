package io.github.shalintripathi.canvas.core

import java.security.MessageDigest
import java.security.SecureRandom

/** A paired phone. Only the SHA-256 of its session is kept. */
data class Phone(val hash: String, val label: String, val pairedAt: Long) {
    val id: String get() = hash.take(12)
}

interface PhoneStore {
    fun load(): List<Phone>
    fun save(phones: List<Phone>)
}

/**
 * Pairing for the phone page. The TV shows a QR code (carrying [Code.token]) and a PIN for typing
 * by hand. Either one, while fresh, turns into a long-lived session cookie for that phone.
 *
 * Wrong PINs are also counted across codes: opening Phone again for a fresh code does not reset
 * that count, so a device on the network cannot guess its way through code after code.
 */
class Pairing(
    private val store: PhoneStore,
    private val clock: () -> Long = System::currentTimeMillis,
    private val random: SecureRandom = SecureRandom(),
) {
    data class Code(val token: String, val pin: String, val expiresAt: Long)

    sealed class PinResult {
        data class Paired(val session: String) : PinResult()
        data class Wrong(val attemptsLeft: Int) : PinResult()
        object NoCode : PinResult()

        /** Too many wrong PINs lately, whatever the code: no PIN is checked until they age out. */
        object Locked : PinResult()
    }

    private var code: Code? = null
    private var wrongPins = 0
    private val recentWrongPins = ArrayDeque<Long>() // when each wrong PIN came, oldest first, across codes
    private val phones: MutableList<Phone> = store.load().toMutableList()

    @Synchronized
    fun newCode(): Code {
        val pin = random.nextInt(1_000_000).toString().padStart(6, '0')
        return Code(hex(bytes(16)), pin, clock() + CODE_TTL_MS).also {
            code = it
            wrongPins = 0
        }
    }

    @Synchronized
    fun currentCode(): Code? = code?.takeIf { clock() < it.expiresAt }

    @Synchronized
    fun pairWithToken(token: String, label: String): String? {
        val c = currentCode() ?: return null
        return if (same(token, c.token)) addPhone(label) else null
    }

    @Synchronized
    fun pairWithPin(pin: String, label: String): PinResult {
        val now = clock()
        while (recentWrongPins.isNotEmpty() && now - recentWrongPins.first() >= LOCKOUT_WINDOW_MS) recentWrongPins.removeFirst()
        if (recentWrongPins.size >= MAX_RECENT_WRONG_PINS) return PinResult.Locked
        val c = currentCode() ?: return PinResult.NoCode
        if (same(pin.trim(), c.pin)) return PinResult.Paired(addPhone(label))
        recentWrongPins.addLast(now)
        wrongPins++
        if (wrongPins >= MAX_WRONG_PINS) code = null
        return when {
            recentWrongPins.size >= MAX_RECENT_WRONG_PINS -> PinResult.Locked
            code == null -> PinResult.NoCode
            else -> PinResult.Wrong(MAX_WRONG_PINS - wrongPins)
        }
    }

    @Synchronized
    fun isPaired(session: String?): Boolean = phoneFor(session) != null

    @Synchronized
    fun phoneFor(session: String?): Phone? {
        if (session.isNullOrEmpty()) return null
        val hash = sha256(session)
        return phones.firstOrNull { same(it.hash, hash) }
    }

    @Synchronized
    fun phones(): List<Phone> = phones.toList()

    @Synchronized
    fun revoke(id: String): Boolean {
        val removed = phones.removeAll { it.id == id }
        if (removed) store.save(phones.toList())
        return removed
    }

    private fun addPhone(label: String): String {
        val session = hex(bytes(32))
        phones.add(Phone(sha256(session), label, clock()))
        while (phones.size > MAX_PHONES) phones.removeAt(0)
        store.save(phones.toList())
        return session
    }

    private fun bytes(n: Int) = ByteArray(n).also { random.nextBytes(it) }

    companion object {
        const val CODE_TTL_MS = 10 * 60 * 1000L
        const val MAX_WRONG_PINS = 5
        const val MAX_RECENT_WRONG_PINS = 10
        const val LOCKOUT_WINDOW_MS = 10 * 60 * 1000L
        const val MAX_PHONES = 20

        fun sha256(s: String): String = hex(MessageDigest.getInstance("SHA-256").digest(s.toByteArray(Charsets.UTF_8)))

        fun deviceLabel(userAgent: String?): String {
            val ua = userAgent.orEmpty()
            return when {
                "iPhone" in ua -> "iPhone"
                "iPad" in ua -> "iPad"
                "Android" in ua -> "Android phone"
                "Macintosh" in ua -> "Mac or iPad"
                "Windows" in ua -> "Windows PC"
                else -> "Browser"
            }
        }

        private fun hex(bytes: ByteArray): String {
            val digits = "0123456789abcdef"
            val out = StringBuilder(bytes.size * 2)
            for (b in bytes) {
                val v = b.toInt() and 0xff
                out.append(digits[v ushr 4]).append(digits[v and 0x0f])
            }
            return out.toString()
        }

        private fun same(a: String, b: String) = MessageDigest.isEqual(a.toByteArray(), b.toByteArray())
    }
}
