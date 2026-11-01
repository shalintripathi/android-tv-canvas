package io.github.shalintripathi.canvas.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

class CaptionsTest {
    private val utc = TimeZone.getTimeZone("UTC")

    private fun at(year: Int, month: Int, day: Int): Long =
        Calendar.getInstance(utc).apply { clear(); set(year, month - 1, day, 12, 0) }.timeInMillis

    @Test
    fun oldPhotosSayHowLongAgo() {
        val now = at(2026, 11, 1)
        assertEquals("March 2023 · 3 years ago", Captions.forPhoto(at(2023, 3, 14), now, Locale.US, utc))
        assertEquals("October 2025 · 1 year ago", Captions.forPhoto(at(2025, 10, 31), now, Locale.US, utc))
    }

    @Test
    fun recentPhotosShowOnlyTheMonth() {
        val now = at(2026, 11, 1)
        assertEquals("June 2026", Captions.forPhoto(at(2026, 6, 2), now, Locale.US, utc))
        assertEquals("December 2025", Captions.forPhoto(at(2025, 12, 25), now, Locale.US, utc))
    }

    @Test
    fun missingOrFutureDatesHaveNoCaption() {
        val now = at(2026, 11, 1)
        assertNull(Captions.forPhoto(null, now, Locale.US, utc))
        assertNull(Captions.forPhoto(0L, now, Locale.US, utc))
        assertNull(Captions.forPhoto(at(2027, 1, 1), now, Locale.US, utc))
    }

    @Test
    fun aYearCountsOnlyAfterTheAnniversary() {
        assertEquals(2, Captions.yearsBetween(at(2024, 2, 29), at(2026, 3, 1), utc))
        assertEquals(1, Captions.yearsBetween(at(2024, 3, 2), at(2026, 3, 1), utc))
    }
}
