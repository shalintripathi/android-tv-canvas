package io.github.shalintripathi.canvas.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LibraryJsonTest {
    private val a = PhotoEntry("abc123def456", 1000L, 900L, 1920, 1080, favorite = true)
    private val b = PhotoEntry("zzz999yyy888", 2000L, null, 1080, 1920)

    @Test
    fun roundTrips() {
        assertEquals(listOf(a, b), LibraryJson.decode(LibraryJson.encode(listOf(a, b))))
    }

    @Test
    fun skipsBrokenEntriesAndBrokenFiles() {
        val json = """[{"id":"../../etc","addedAt":1,"width":10,"height":10},
            {"id":"abc123def456","addedAt":1,"width":0,"height":10},
            {"id":"zzz999yyy888","addedAt":2000,"takenAt":null,"width":1080,"height":1920,"favorite":false,"source":"phone"}]"""
        assertEquals(listOf(b), LibraryJson.decode(json))
        assertEquals(emptyList<PhotoEntry>(), LibraryJson.decode("not json"))
    }

    @Test
    fun portraitAndRefFollowTheSize() {
        assertFalse(a.portrait)
        assertTrue(b.portrait)
        assertEquals(PhotoRef("abc123def456", portrait = false, favorite = true), a.toRef())
    }

    @Test
    fun idsAreTwelveLowercaseAlphanumerics() {
        val id = LibraryJson.newId()
        assertTrue(LibraryJson.isValidId(id))
        assertFalse(LibraryJson.isValidId("ABC123DEF456"))
        assertFalse(LibraryJson.isValidId("abc/23def456"))
        assertFalse(LibraryJson.isValidId("short"))
    }

    @Test
    fun phonesRoundTrip() {
        val phone = Phone("a".repeat(64), "iPhone", 42L)
        assertEquals(listOf(phone), PhonesJson.decode(PhonesJson.encode(listOf(phone))))
        assertEquals(emptyList<Phone>(), PhonesJson.decode("""[{"hash":"short","label":"x","pairedAt":1}]"""))
    }
}
