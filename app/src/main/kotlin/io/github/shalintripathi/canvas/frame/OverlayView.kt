package io.github.shalintripathi.canvas.frame

import android.content.Context
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.text.format.DateFormat
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import io.github.shalintripathi.canvas.ui.MATCH
import io.github.shalintripathi.canvas.ui.WRAP
import io.github.shalintripathi.canvas.ui.dp
import io.github.shalintripathi.canvas.ui.text
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.Random

/** Clock and date in one corner, the photo's caption in the other. Drifts a little to avoid burn-in. */
class OverlayView(context: Context, showClock: Boolean) : FrameLayout(context) {
    private val main = Handler(Looper.getMainLooper())
    private val random = Random()
    private val block = FrameLayout(context)
    private val clock = context.text(46f, "sans-serif-light", Color.WHITE)
    private val date = context.text(13f, "sans-serif-medium", 0xCCFFFFFF.toInt())
    private val caption = context.text(16f, "sans-serif", 0xE6FFFFFF.toInt())

    private val tick = object : Runnable {
        override fun run() {
            updateClock()
            main.postDelayed(this, 60_000 - System.currentTimeMillis() % 60_000)
        }
    }
    private val shift = object : Runnable {
        override fun run() {
            moveBlock()
            main.postDelayed(this, SHIFT_MS)
        }
    }

    init {
        val scrim = View(context)
        scrim.background = GradientDrawable(GradientDrawable.Orientation.BOTTOM_TOP, intArrayOf(0x99000000.toInt(), 0))
        addView(scrim, LayoutParams(MATCH, dp(240), Gravity.BOTTOM))

        val corner = LinearLayout(context)
        corner.orientation = LinearLayout.VERTICAL
        corner.gravity = Gravity.END
        clock.includeFontPadding = false
        date.letterSpacing = 0.18f
        corner.addView(clock)
        corner.addView(date)
        corner.visibility = if (showClock) View.VISIBLE else View.GONE
        block.addView(corner, LayoutParams(WRAP, WRAP, Gravity.BOTTOM or Gravity.END))

        caption.alpha = 0f
        block.addView(caption, LayoutParams(WRAP, WRAP, Gravity.BOTTOM or Gravity.START))
        addView(block, LayoutParams(MATCH, MATCH).apply { setMargins(dp(56), dp(48), dp(56), dp(48)) })

        for (t in listOf(clock, date, caption)) t.setShadowLayer(dp(10).toFloat(), 0f, dp(1).toFloat(), 0xB3000000.toInt())
    }

    fun start() {
        stop()
        tick.run()
        main.postDelayed(shift, SHIFT_MS)
    }

    fun stop() {
        main.removeCallbacks(tick)
        main.removeCallbacks(shift)
    }

    fun setCaption(text: String?) {
        caption.animate().cancel()
        caption.animate().alpha(0f).setDuration(300).withEndAction {
            caption.text = text.orEmpty()
            if (text != null) caption.animate().alpha(1f).setDuration(700).start()
        }.start()
    }

    private fun updateClock() {
        val now = Date()
        val pattern = if (DateFormat.is24HourFormat(context)) "H:mm" else "h:mm"
        clock.text = SimpleDateFormat(pattern, Locale.getDefault()).format(now)
        date.text = SimpleDateFormat("EEEE d MMMM", Locale.getDefault()).format(now).uppercase(Locale.getDefault())
    }

    private fun moveBlock() {
        val r = dp(24)
        block.animate()
            .translationX((random.nextInt(2 * r + 1) - r).toFloat())
            .translationY(-random.nextInt(r + 1).toFloat())
            .setDuration(2000)
            .start()
    }

    private companion object {
        const val SHIFT_MS = 5 * 60_000L
    }
}
