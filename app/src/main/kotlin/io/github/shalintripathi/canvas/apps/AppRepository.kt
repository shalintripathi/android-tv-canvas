package io.github.shalintripathi.canvas.apps

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.drawable.Drawable
import android.os.Build
import io.github.shalintripathi.canvas.ui.darken
import java.io.ByteArrayOutputStream
import java.util.concurrent.ConcurrentHashMap

class AppInfo(
    val pkg: String,
    val label: String,
    val component: ComponentName,
    val art: Drawable,
    val isBanner: Boolean,
    val color: Int,
)

/** Installed TV apps: everything with a Leanback launcher entry. */
class AppRepository(private val context: Context) {
    private val pm = context.packageManager
    private val colors = ConcurrentHashMap<String, Int>()

    @Volatile
    private var sequence = 0

    /** True if packages were installed, removed or changed since [markSeen]. Always true before Android 8. */
    fun hasChanges(): Boolean = Build.VERSION.SDK_INT < 26 || pm.getChangedPackages(sequence) != null

    fun markSeen() {
        if (Build.VERSION.SDK_INT >= 26) pm.getChangedPackages(sequence)?.let { sequence = it.sequenceNumber }
    }

    /** One entry per package, sorted by label, with banner art and a glow colour. */
    fun load(excluded: Set<String>): List<AppInfo> {
        val byPkg = LinkedHashMap<String, AppInfo>()
        for (ri in pm.queryIntentActivities(leanback(), 0)) {
            val ai = ri.activityInfo
            if (ai.packageName in excluded || byPkg.containsKey(ai.packageName)) continue
            val banner = ai.loadBanner(pm) ?: ai.applicationInfo.loadBanner(pm)
            val art = banner ?: ri.loadIcon(pm)
            byPkg[ai.packageName] = AppInfo(
                ai.packageName, ri.loadLabel(pm).toString(), ComponentName(ai.packageName, ai.name),
                art, banner != null, sampleColor(ai.packageName, art),
            )
        }
        return byPkg.values.sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.label })
    }

    /** Package and label only: cheap enough for the phone page. */
    fun labels(excluded: Set<String>): List<Pair<String, String>> =
        pm.queryIntentActivities(leanback(), 0)
            .map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
            .filter { it.first !in excluded }
            .distinctBy { it.first }
            .sortedWith(compareBy(String.CASE_INSENSITIVE_ORDER) { it.second })

    /** Never shown as tiles: Canvas itself, Settings (a pill) and the Live TV app (in Inputs). */
    fun excludedPackages(): Set<String> = setOf(context.packageName, TV_SETTINGS) + LIVE_TV.filter { isInstalled(it) }

    fun liveTvPackage(): String? = LIVE_TV.firstOrNull { isInstalled(it) }

    fun isInstalled(pkg: String): Boolean = try {
        pm.getPackageInfo(pkg, 0)
        true
    } catch (e: PackageManager.NameNotFoundException) {
        false
    }

    fun launchIntent(pkg: String): Intent? = pm.getLeanbackLaunchIntentForPackage(pkg) ?: pm.getLaunchIntentForPackage(pkg)

    /** The app's banner, or its icon on a tinted card, as a PNG for the phone page. */
    fun artPng(pkg: String, width: Int = 320, height: Int = 180): ByteArray? {
        val component = pm.getLeanbackLaunchIntentForPackage(pkg)?.component ?: return null
        val banner = try {
            pm.getActivityBanner(component) ?: pm.getApplicationBanner(pkg)
        } catch (e: PackageManager.NameNotFoundException) {
            null
        }
        val shared = banner ?: try {
            pm.getActivityIcon(component)
        } catch (e: PackageManager.NameNotFoundException) {
            return null
        }
        // This runs on the phone page's threads, and the package manager's drawables can share state with the ones
        // the home screen is drawing: bound and draw a copy of our own.
        val art = shared.mutate()
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        if (banner != null) {
            art.setBounds(0, 0, width, height)
        } else {
            canvas.drawColor(darken(sampleColor(pkg, art), 0.45f))
            val size = height * 2 / 3
            art.setBounds((width - size) / 2, (height - size) / 2, (width + size) / 2, (height + size) / 2)
        }
        art.draw(canvas)
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
        bitmap.recycle()
        return out.toByteArray()
    }

    /** Average the art's vivid pixels, then push saturation so the glow reads as a colour. */
    private fun sampleColor(pkg: String, drawable: Drawable): Int = colors.getOrPut(pkg) {
        try {
            val bitmap = Bitmap.createBitmap(24, 14, Bitmap.Config.ARGB_8888)
            drawable.setBounds(0, 0, 24, 14)
            drawable.draw(Canvas(bitmap))
            val hsv = FloatArray(3)
            var r = 0.0
            var g = 0.0
            var b = 0.0
            var weight = 0.0
            for (y in 0 until 14) for (x in 0 until 24) {
                val p = bitmap.getPixel(x, y)
                if (Color.alpha(p) < 128) continue
                Color.colorToHSV(p, hsv)
                val w = 0.05 + hsv[1] * hsv[2]
                r += Color.red(p) * w
                g += Color.green(p) * w
                b += Color.blue(p) * w
                weight += w
            }
            bitmap.recycle()
            if (weight == 0.0) {
                DEFAULT_COLOR
            } else {
                Color.colorToHSV(Color.rgb((r / weight).toInt(), (g / weight).toInt(), (b / weight).toInt()), hsv)
                if (hsv[1] > 0.12f) {
                    hsv[1] = maxOf(hsv[1], 0.75f)
                    hsv[2] = 0.85f
                } else {
                    hsv[1] = 0f
                    hsv[2] = 0.45f
                }
                Color.HSVToColor(hsv)
            }
        } catch (e: RuntimeException) {
            DEFAULT_COLOR
        }
    }

    companion object {
        const val DEFAULT_COLOR = 0xFF3A3A3A.toInt()
        private const val TV_SETTINGS = "com.android.tv.settings"

        /** Live TV / tuner apps, most specific first. */
        val LIVE_TV = listOf(
            "com.mediatek.tv.oneworld.tvcenter",
            "com.mediatek.wwtv.tvcenter",
            "com.tcl.tv",
            "com.sony.dtv.tvx",
            "com.google.android.tv",
        )

        private fun leanback() = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER)
    }
}
