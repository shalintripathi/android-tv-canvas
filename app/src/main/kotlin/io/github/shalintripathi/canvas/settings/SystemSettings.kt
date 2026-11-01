package io.github.shalintripathi.canvas.settings

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings

/**
 * Screensaver and timeout settings. Writing needs WRITE_SECURE_SETTINGS and WRITE_SETTINGS, which
 * `tools/canvas install` grants over ADB. Android 12+ hides some Secure settings from apps, so reads
 * may come back null; writes still work.
 */
class SystemSettings(private val context: Context) {
    private val resolver = context.contentResolver
    val dream = ComponentName(context.packageName, DREAM_CLASS)

    fun canWriteSecure(): Boolean =
        context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    fun canWriteSystem(): Boolean = Settings.System.canWrite(context)

    fun canChange(): Boolean = canWriteSecure() && canWriteSystem()

    /** True or false when readable; null when the TV hides the setting from apps. */
    fun isCanvasScreensaver(): Boolean? = readSecure(SCREENSAVER_COMPONENTS)?.let { value ->
        value.split(',').firstOrNull()?.let { ComponentName.unflattenFromString(it) } == dream
    }

    fun useCanvas(): Boolean = canWriteSecure() &&
        putSecure(SCREENSAVER_COMPONENTS, dream.flattenToString()) &&
        putSecure(SCREENSAVER_ENABLED, "1") &&
        putSecure(SCREENSAVER_ON_SLEEP, "1")

    fun restorePrevious(prefs: Prefs): Boolean {
        if (!canWriteSecure()) return false
        // Android 12 hides screensaver_components from apps, so the TV's own one is often unknown: use Google TV's.
        val previous = prefs.previousScreensaver ?: GOOGLE_TV_SCREENSAVER.takeIf { isInstalled(GOOGLE_TV_PACKAGE) }
        return if (previous != null) putSecure(SCREENSAVER_COMPONENTS, previous) else putSecure(SCREENSAVER_ENABLED, "0")
    }

    private fun isInstalled(pkg: String): Boolean = try {
        context.packageManager.getPackageInfo(pkg, 0)
        true
    } catch (e: PackageManager.NameNotFoundException) {
        false
    }

    /** Called at startup: note the TV's own screensaver once, before Canvas replaces it. */
    fun rememberPrevious(prefs: Prefs) {
        if (prefs.previousScreensaver != null) return
        val current = readSecure(SCREENSAVER_COMPONENTS) ?: return
        if (current.isNotEmpty() && !current.startsWith(context.packageName)) prefs.previousScreensaver = current
    }

    /** Puts Canvas back as the screensaver if a TV update or Google's Ambient settings replaced it. */
    fun repair(prefs: Prefs) {
        if (prefs.useCanvasScreensaver && canWriteSecure() && isCanvasScreensaver() != true) useCanvas()
    }

    fun startAfterMinutes(): Int =
        Settings.System.getInt(resolver, Settings.System.SCREEN_OFF_TIMEOUT, 600_000) / 60_000

    fun setStartAfterMinutes(minutes: Int): Boolean = canWriteSystem() && try {
        Settings.System.putInt(resolver, Settings.System.SCREEN_OFF_TIMEOUT, minutes * 60_000)
    } catch (e: RuntimeException) {
        false
    }

    /** Minutes on the screensaver before the TV sleeps; -1 means never. */
    fun turnOffAfterMinutes(prefs: Prefs): Int {
        val ms = readSecure(SLEEP_TIMEOUT)?.toIntOrNull() ?: prefs.turnOffAfterMs
        return if (ms < 0) -1 else ms / 60_000
    }

    fun setTurnOffAfterMinutes(minutes: Int, prefs: Prefs): Boolean {
        val ms = if (minutes < 0) -1 else minutes * 60_000
        val ok = canWriteSecure() && putSecure(SLEEP_TIMEOUT, ms.toString())
        if (ok) prefs.turnOffAfterMs = ms
        return ok
    }

    private fun readSecure(key: String): String? = try {
        Settings.Secure.getString(resolver, key)
    } catch (e: SecurityException) {
        null
    }

    private fun putSecure(key: String, value: String): Boolean = try {
        Settings.Secure.putString(resolver, key, value)
    } catch (e: RuntimeException) {
        false
    }

    companion object {
        const val DREAM_CLASS = "io.github.shalintripathi.canvas.frame.FrameDreamService"
        private const val SCREENSAVER_COMPONENTS = "screensaver_components"
        private const val SCREENSAVER_ENABLED = "screensaver_enabled"
        private const val SCREENSAVER_ON_SLEEP = "screensaver_activate_on_sleep"
        private const val SLEEP_TIMEOUT = "sleep_timeout"
        private const val GOOGLE_TV_PACKAGE = "com.google.android.apps.tv.dreamx"
        private const val GOOGLE_TV_SCREENSAVER = "$GOOGLE_TV_PACKAGE/.service.Backdrop"
    }
}
