package io.github.shalintripathi.canvas.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    private class MemoryStore : PhoneStore {
        var saved: List<Phone> = emptyList()
        override fun load() = saved
        override fun save(phones: List<Phone>) {
            saved = phones
        }
    }

    private var now = 1_000_000L
    private val store = MemoryStore()
    private val pairing = Pairing(store, { now })

    @Test
    fun codesHaveATokenAndASixDigitPin() {
        val code = pairing.newCode()
        assertEquals(32, code.token.length)
        assertTrue(code.pin.matches(Regex("\\d{6}")))
        assertEquals(now + Pairing.CODE_TTL_MS, code.expiresAt)
    }

    @Test
    fun theQrTokenPairsAPhoneAndOnlyItsHashIsStored() {
        val code = pairing.newCode()
        val session = pairing.pairWithToken(code.token, "Android phone")
        assertNotNull(session)
        assertTrue(pairing.isPaired(session))
        assertEquals(Pairing.sha256(session!!), store.saved.single().hash)
        assertFalse(store.saved.single().hash.contains(session))
    }

    @Test
    fun wrongOrExpiredTokensFail() {
        val code = pairing.newCode()
        assertNull(pairing.pairWithToken("nope", "x"))
        now += Pairing.CODE_TTL_MS
        assertNull(pairing.pairWithToken(code.token, "x"))
    }

    @Test
    fun fiveWrongPinsVoidTheCode() {
        val code = pairing.newCode()
        repeat(Pairing.MAX_WRONG_PINS - 1) { i ->
            assertEquals(Pairing.PinResult.Wrong(Pairing.MAX_WRONG_PINS - 1 - i), pairing.pairWithPin("x", "x"))
        }
        assertEquals(Pairing.PinResult.NoCode, pairing.pairWithPin("x", "x"))
        assertEquals(Pairing.PinResult.NoCode, pairing.pairWithPin(code.pin, "x"))
    }

    @Test
    fun theRightPinPairs() {
        val code = pairing.newCode()
        val result = pairing.pairWithPin(" ${code.pin} ", "iPhone")
        assertTrue(result is Pairing.PinResult.Paired)
        assertEquals("iPhone", pairing.phoneFor((result as Pairing.PinResult.Paired).session)?.label)
    }

    @Test
    fun revokedPhonesAreLockedOut() {
        val session = pairing.pairWithToken(pairing.newCode().token, "x")!!
        val id = pairing.phones().single().id
        assertTrue(pairing.revoke(id))
        assertFalse(pairing.isPaired(session))
        assertTrue(store.saved.isEmpty())
    }

    @Test
    fun pairedPhonesSurviveARestart() {
        val session = pairing.pairWithToken(pairing.newCode().token, "x")!!
        assertTrue(Pairing(store, { now }).isPaired(session))
    }

    @Test
    fun missingSessionsAreNotPaired() {
        assertFalse(pairing.isPaired(null))
        assertFalse(pairing.isPaired(""))
    }

    @Test
    fun labelsComeFromTheUserAgent() {
        assertEquals("iPhone", Pairing.deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"))
        assertEquals("Android phone", Pairing.deviceLabel("Mozilla/5.0 (Linux; Android 15; Pixel 9)"))
        assertEquals("Browser", Pairing.deviceLabel(null))
    }

    @Test
    fun twoPhonesCanShareOneCode() {
        val code = pairing.newCode()
        val a = pairing.pairWithToken(code.token, "Phone A")
        val bResult = pairing.pairWithPin(code.pin, "Phone B")
        val c = pairing.pairWithToken(code.token, "Phone C")
        assertNotNull(a)
        assertTrue(bResult is Pairing.PinResult.Paired)
        assertNotNull(c)
        val b = (bResult as Pairing.PinResult.Paired).session
        assertEquals(3, setOf(a, b, c).size)
        assertTrue(pairing.isPaired(a))
        assertTrue(pairing.isPaired(b))
        assertTrue(pairing.isPaired(c))
        assertEquals(3, pairing.phones().size)
    }

    @Test
    fun sessionsAreLongRandomHex() {
        val code = pairing.newCode()
        val byToken = pairing.pairWithToken(code.token, "x")
        val byPin = (pairing.pairWithPin(code.pin, "y") as Pairing.PinResult.Paired).session
        assertNotNull(byToken)
        assertTrue(byToken!!.matches(Regex("[0-9a-f]{64}")))
        assertTrue(byPin.matches(Regex("[0-9a-f]{64}")))
    }
}
