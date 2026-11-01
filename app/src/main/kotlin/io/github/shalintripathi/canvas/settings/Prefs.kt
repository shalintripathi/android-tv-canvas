package io.github.shalintripathi.canvas.settings

import android.content.Context
import android.content.SharedPreferences
import io.github.shalintripathi.canvas.core.HomeLayout

class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("canvas", Context.MODE_PRIVATE)

    /** Dock packages in order; null until the owner changes the dock (then defaults apply). */
    val dock: List<String>?
        get() = sp.getString(DOCK, null)?.split(',')?.filter { it.isNotBlank() }

    val hidden: Set<String>
        get() = sp.getString(HIDDEN, "").orEmpty().split(',').filter { it.isNotBlank() }.toSet()

    var wallpaper: String
        get() = sp.getString(WALLPAPER, WALLPAPER_PHOTOS) ?: WALLPAPER_PHOTOS
        set(value) = sp.edit().putString(WALLPAPER, value).apply()

    var useCanvasScreensaver: Boolean
        get() = sp.getBoolean(USE_CANVAS, true)
        set(value) = sp.edit().putBoolean(USE_CANVAS, value).apply()

    var slideSeconds: Int
        get() = sp.getInt(SLIDE_SECONDS, 30).coerceIn(10, 300)
        set(value) = sp.edit().putInt(SLIDE_SECONDS, value.coerceIn(10, 300)).apply()

    var showClock: Boolean
        get() = sp.getBoolean(SHOW_CLOCK, true)
        set(value) = sp.edit().putBoolean(SHOW_CLOCK, value).apply()

    /** Last "turn TV off after" we wrote, for TVs that hide the setting from apps. */
    var turnOffAfterMs: Int
        get() = sp.getInt(TURN_OFF_MS, 900_000)
        set(value) = sp.edit().putInt(TURN_OFF_MS, value).apply()

    /** The screensaver that was set before Canvas, restored when the owner turns Canvas off. */
    var previousScreensaver: String?
        get() = sp.getString(PREVIOUS_SCREENSAVER, null)
        set(value) = sp.edit().putString(PREVIOUS_SCREENSAVER, value).apply()

    /** The dock and hidden apps, with the dock limited to installed apps and defaults until edited. */
    fun layout(installed: Collection<String>): HomeLayout {
        val order = (dock ?: HomeLayout.defaults(installed).dock).filter { it in installed }
        return HomeLayout(order, hidden)
    }

    fun saveLayout(layout: HomeLayout) {
        sp.edit()
            .putString(DOCK, layout.dock.joinToString(","))
            .putString(HIDDEN, layout.hidden.joinToString(","))
            .apply()
    }

    fun register(listener: SharedPreferences.OnSharedPreferenceChangeListener) =
        sp.registerOnSharedPreferenceChangeListener(listener)

    fun unregister(listener: SharedPreferences.OnSharedPreferenceChangeListener) =
        sp.unregisterOnSharedPreferenceChangeListener(listener)

    companion object {
        const val DOCK = "dock"
        const val HIDDEN = "hidden"
        const val WALLPAPER = "wallpaper"
        const val WALLPAPER_PHOTOS = "photos"
        const val WALLPAPER_BUILTIN = "builtin"
        private const val USE_CANVAS = "use_canvas_screensaver"
        private const val SLIDE_SECONDS = "slide_seconds"
        private const val SHOW_CLOCK = "show_clock"
        private const val TURN_OFF_MS = "turn_off_ms"
        private const val PREVIOUS_SCREENSAVER = "previous_screensaver"
    }
}
