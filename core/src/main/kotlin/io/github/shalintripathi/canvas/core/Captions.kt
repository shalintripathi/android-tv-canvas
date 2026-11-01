package io.github.shalintripathi.canvas.core

import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

object Captions {
    private const val DAY_MS = 24L * 60 * 60 * 1000

    /** "March 2023 · 3 years ago"; just "June 2026" for photos under a year old; null without a date. */
    fun forPhoto(
        takenAt: Long?,
        now: Long,
        locale: Locale = Locale.getDefault(),
        zone: TimeZone = TimeZone.getDefault(),
    ): String? {
        if (takenAt == null || takenAt <= 0 || takenAt > now + DAY_MS) return null
        val month = SimpleDateFormat("MMMM yyyy", locale).apply { timeZone = zone }.format(Date(takenAt))
        return when (val years = yearsBetween(takenAt, now, zone)) {
            0 -> month
            1 -> "$month · 1 year ago"
            else -> "$month · $years years ago"
        }
    }

    /** Whole years from [from] to [to]; a year counts once its anniversary has passed. */
    fun yearsBetween(from: Long, to: Long, zone: TimeZone): Int {
        val a = Calendar.getInstance(zone).apply { timeInMillis = from }
        val b = Calendar.getInstance(zone).apply { timeInMillis = to }
        var years = b.get(Calendar.YEAR) - a.get(Calendar.YEAR)
        val beforeAnniversary = b.get(Calendar.MONTH) < a.get(Calendar.MONTH) ||
            (b.get(Calendar.MONTH) == a.get(Calendar.MONTH) &&
                b.get(Calendar.DAY_OF_MONTH) < a.get(Calendar.DAY_OF_MONTH))
        if (beforeAnniversary) years--
        return maxOf(0, years)
    }
}
