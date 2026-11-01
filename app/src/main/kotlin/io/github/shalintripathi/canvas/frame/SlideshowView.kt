package io.github.shalintripathi.canvas.frame

import android.animation.Animator
import android.animation.ObjectAnimator
import android.animation.PropertyValuesHolder
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.PorterDuff
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.view.animation.LinearInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import io.github.shalintripathi.canvas.canvas
import io.github.shalintripathi.canvas.core.Captions
import io.github.shalintripathi.canvas.core.Slide
import io.github.shalintripathi.canvas.core.SlidePlanner
import io.github.shalintripathi.canvas.ui.MATCH
import io.github.shalintripathi.canvas.ui.dp
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import kotlin.random.Random

/**
 * The slide engine behind both the screensaver and the home wallpaper. Decodes off the main thread,
 * crossfades between slides, puts two portraits side by side, and optionally drifts each photo
 * (Ken Burns) under a clock-and-caption overlay.
 */
class SlideshowView(context: Context, private val config: Config) : FrameLayout(context) {

    data class Config(val holdMs: Long, val kenBurns: Boolean, val overlay: Boolean, val showClock: Boolean)

    /** Show only the built-in photos, even when the library has some. */
    var builtinOnly = false
        set(value) {
            if (field == value) return
            field = value
            reloadPhotos()
        }

    private class Decoded(val bitmaps: List<Bitmap>, val backdrop: Bitmap?, val duo: Boolean)

    private val app = context.canvas()
    private val planner = SlidePlanner()
    private val main = Handler(Looper.getMainLooper())
    private val decoder: ExecutorService = Executors.newSingleThreadExecutor()
    private val overlay: OverlayView? = if (config.overlay) OverlayView(context, config.showClock) else null
    private var front: FrameLayout? = null
    private var running = false
    @Volatile
    private var generation = 0
    private var shownAt = 0L
    private val advance = Runnable { show(planner.next()) }
    private val onLibraryChanged: () -> Unit = { reloadPhotos() }

    init {
        setBackgroundColor(Color.BLACK)
        overlay?.let { addView(it, LayoutParams(MATCH, MATCH)) }
        reloadPhotos()
    }

    fun start() {
        if (running) return
        running = true
        // The library may have changed while stopped (a phone upload, a delete), and nobody was listening.
        reloadPhotos()
        app.library.addListener(onLibraryChanged)
        overlay?.start()
        if (front == null) show(planner.next()) else schedule(config.holdMs - (SystemClock.elapsedRealtime() - shownAt))
    }

    fun stop() {
        if (!running) return
        running = false
        main.removeCallbacks(advance)
        app.library.removeListener(onLibraryChanged)
        overlay?.stop()
    }

    fun release() {
        stop()
        decoder.shutdownNow()
        front?.let { removeLayer(it) }
        front = null
    }

    fun next() {
        if (!running) return
        main.removeCallbacks(advance)
        show(planner.next())
    }

    fun previous() {
        if (!running) return
        val slide = planner.previous() ?: return
        main.removeCallbacks(advance)
        show(slide)
    }

    private fun reloadPhotos() {
        val library = if (builtinOnly) emptyList() else app.library.all().map { it.toRef() }
        planner.setPhotos(library.ifEmpty { BitmapLoader.builtins(context) })
    }

    private fun schedule(delay: Long = config.holdMs) {
        main.removeCallbacks(advance)
        if (running) main.postDelayed(advance, delay.coerceAtLeast(1000))
    }

    private fun show(slide: Slide?) {
        if (slide == null) {
            schedule()
            return
        }
        val gen = ++generation
        val w = if (width > 0) width else resources.displayMetrics.widthPixels
        val h = if (height > 0) height else resources.displayMetrics.heightPixels
        try {
            decoder.execute {
                // A newer slide was asked for while this one waited its turn: skip the decode.
                if (gen != generation) return@execute
                val decoded = decode(slide, w, h)
                main.post {
                    if (gen != generation || !running) return@post
                    if (decoded == null) {
                        schedule(2000)
                        return@post
                    }
                    crossfadeTo(layerFor(decoded), decoded.duo)
                    overlay?.setCaption(caption(slide))
                    shownAt = SystemClock.elapsedRealtime()
                    schedule()
                }
            }
        } catch (e: RejectedExecutionException) {
            // Released while a slide was pending.
        }
    }

    private fun caption(slide: Slide): String? {
        val takenAt = slide.photos.firstNotNullOfOrNull { app.library.get(it.id)?.takenAt }
        return Captions.forPhoto(takenAt, System.currentTimeMillis())
    }

    private fun decode(slide: Slide, w: Int, h: Int): Decoded? = try {
        when (slide) {
            is Slide.Single -> BitmapLoader.load(context, slide.photo.id, w, h)?.let { bitmap ->
                Decoded(listOf(bitmap), if (slide.photo.portrait) BitmapLoader.backdrop(bitmap) else null, duo = false)
            }
            is Slide.Duo -> {
                val left = BitmapLoader.load(context, slide.left.id, w / 2, h)
                val right = BitmapLoader.load(context, slide.right.id, w / 2, h)
                if (left == null || right == null) null else Decoded(listOf(left, right), null, duo = true)
            }
        }
    } catch (e: OutOfMemoryError) {
        null
    } catch (e: Exception) {
        // Nothing may escape on the decoder thread: it would kill the process, and the screensaver with it.
        null
    }

    private fun layerFor(d: Decoded): FrameLayout {
        val layer = FrameLayout(context)
        when {
            d.duo -> {
                val row = LinearLayout(context)
                d.bitmaps.forEachIndexed { i, bitmap ->
                    val params = LinearLayout.LayoutParams(0, MATCH, 1f)
                    if (i > 0) params.leftMargin = dp(6)
                    row.addView(image(bitmap, ImageView.ScaleType.CENTER_CROP), params)
                }
                layer.addView(row, LayoutParams(MATCH, MATCH))
            }
            d.backdrop != null -> {
                val back = image(d.backdrop, ImageView.ScaleType.CENTER_CROP)
                back.setColorFilter(0x8C000000.toInt(), PorterDuff.Mode.SRC_ATOP)
                layer.addView(back, LayoutParams(MATCH, MATCH))
                layer.addView(image(d.bitmaps[0], ImageView.ScaleType.FIT_CENTER), LayoutParams(MATCH, MATCH))
            }
            else -> layer.addView(image(d.bitmaps[0], ImageView.ScaleType.CENTER_CROP), LayoutParams(MATCH, MATCH))
        }
        return layer
    }

    private fun image(bitmap: Bitmap, scale: ImageView.ScaleType) = ImageView(context).apply {
        scaleType = scale
        setImageBitmap(bitmap)
    }

    private fun crossfadeTo(layer: FrameLayout, gentle: Boolean) {
        val old = front
        if (old != null) {
            // Skipped through quickly: the previous fade is unfinished and older layers still sit under it.
            // Settle it now so at most two photo layers (this one and the one it fades over) are ever attached.
            if (old.alpha < 1f) {
                old.animate().cancel()
                old.alpha = 1f
            }
            for (i in childCount - 1 downTo 0) {
                val child = getChildAt(i)
                if (child !== old && child !== overlay && child is FrameLayout) removeLayer(child)
            }
        }
        addView(layer, overlay?.let { indexOfChild(it) } ?: childCount, LayoutParams(MATCH, MATCH))
        front = layer
        layer.alpha = 0f
        layer.animate()
            .alpha(1f)
            .setDuration(if (old == null) FIRST_FADE_MS else FADE_MS)
            .withLayer()
            .withEndAction { if (old != null) removeLayer(old) }
            .start()
        if (config.kenBurns) startKenBurns(layer, gentle)
    }

    /** A slow zoom and drift that never shows the photo's edges. */
    private fun startKenBurns(layer: View, gentle: Boolean) {
        val zoom = if (gentle) 1.04f else KEN_BURNS_ZOOM
        val w = if (width > 0) width else resources.displayMetrics.widthPixels
        val h = if (height > 0) height else resources.displayMetrics.heightPixels
        val dx = (Random.nextFloat() - 0.5f) * w * (zoom - 1f)
        val dy = (Random.nextFloat() - 0.5f) * h * (zoom - 1f)
        val zoomIn = Random.nextBoolean()
        val animator = ObjectAnimator.ofPropertyValuesHolder(
            layer,
            PropertyValuesHolder.ofFloat(View.SCALE_X, if (zoomIn) 1f else zoom, if (zoomIn) zoom else 1f),
            PropertyValuesHolder.ofFloat(View.SCALE_Y, if (zoomIn) 1f else zoom, if (zoomIn) zoom else 1f),
            PropertyValuesHolder.ofFloat(View.TRANSLATION_X, if (zoomIn) 0f else dx, if (zoomIn) dx else 0f),
            PropertyValuesHolder.ofFloat(View.TRANSLATION_Y, if (zoomIn) 0f else dy, if (zoomIn) dy else 0f),
        )
        animator.duration = config.holdMs + 2 * FADE_MS
        animator.interpolator = LinearInterpolator()
        animator.start()
        layer.tag = animator
    }

    private fun removeLayer(layer: FrameLayout) {
        (layer.tag as? Animator)?.cancel()
        layer.animate().cancel()
        removeView(layer)
        clearImages(layer)
    }

    private fun clearImages(view: View) {
        when (view) {
            is ImageView -> view.setImageDrawable(null)
            is ViewGroup -> for (i in 0 until view.childCount) clearImages(view.getChildAt(i))
        }
    }

    private companion object {
        const val FADE_MS = 1500L
        const val FIRST_FADE_MS = 600L
        const val KEN_BURNS_ZOOM = 1.08f
    }
}
