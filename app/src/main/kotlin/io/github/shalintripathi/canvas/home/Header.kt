package io.github.shalintripathi.canvas.home

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.provider.Settings
import android.text.format.DateFormat
import android.view.Gravity
import android.view.View
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import io.github.shalintripathi.canvas.ui.WRAP
import io.github.shalintripathi.canvas.ui.dp
import io.github.shalintripathi.canvas.ui.launch
import io.github.shalintripathi.canvas.ui.linearParams
import io.github.shalintripathi.canvas.ui.roundRect
import io.github.shalintripathi.canvas.ui.text
import io.github.shalintripathi.canvas.util.Network
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/** Greeting, clock and date on the left; Inputs, network, Phone and Settings pills on the right. */
class Header(
    private val context: Context,
    onInputs: (View) -> Unit,
    onPhone: () -> Unit,
    private val onPillFocused: (View) -> Unit,
) {
    val view = FrameLayout(context)
    val pills: List<TextView>
    private val greeting = context.text(10f, "sans-serif-medium", 0xB3FFFFFF.toInt())
    private val clock = context.text(54f, "sans-serif-thin", Color.WHITE)
    private val ampm = context.text(12f, "sans-serif-light", 0xCCFFFFFF.toInt())
    private val date = context.text(10f, "sans-serif-medium", 0x99FFFFFF.toInt())
    private val network: TextView

    init {
        view.clipChildren = false
        val left = LinearLayout(context)
        left.orientation = LinearLayout.VERTICAL
        greeting.letterSpacing = 0.25f
        greeting.setSingleLine(true)
        greeting.setPadding(0, 0, context.dp(16), 0)
        left.addView(greeting)
        val clockRow = LinearLayout(context)
        clockRow.gravity = Gravity.BOTTOM
        clock.includeFontPadding = false
        clockRow.addView(clock)
        clockRow.addView(ampm, linearParams(WRAP, WRAP, context.dp(6), 0, 0, context.dp(8)))
        left.addView(clockRow, linearParams(WRAP, WRAP, 0, context.dp(2), 0, context.dp(2)))
        date.setSingleLine(true)
        date.setPadding(0, 0, context.dp(16), 0) // letterSpacing is not counted in the measured width
        date.letterSpacing = 0.22f
        left.addView(date)
        for (t in listOf(greeting, clock, ampm, date)) {
            t.setShadowLayer(context.dp(10).toFloat(), 0f, context.dp(1).toFloat(), 0xB3000000.toInt())
        }
        view.addView(left, FrameLayout.LayoutParams(context.dp(420), WRAP, Gravity.START or Gravity.TOP))

        val inputs = pill("⇥", "Inputs").apply { setOnClickListener { onInputs(it) } }
        network = pill("◉", "Wi-Fi").apply { setOnClickListener { context.launch(Intent(Settings.ACTION_WIFI_SETTINGS)) } }
        val phone = pill("▣", "Phone").apply { setOnClickListener { onPhone() } }
        val settings = pill("⚙", "Settings").apply { setOnClickListener { context.launch(Intent(Settings.ACTION_SETTINGS)) } }
        pills = listOf(inputs, network, phone, settings)
        val right = LinearLayout(context)
        right.gravity = Gravity.CENTER_VERTICAL
        right.clipChildren = false
        for (p in pills) right.addView(p, linearParams(WRAP, context.dp(30), context.dp(10), 0, 0, 0))
        view.addView(right, FrameLayout.LayoutParams(WRAP, WRAP, Gravity.END or Gravity.TOP))
    }

    fun update() {
        val now = Calendar.getInstance()
        val hour = now.get(Calendar.HOUR_OF_DAY)
        greeting.text = when {
            hour < 5 -> "GOOD NIGHT"
            hour < 12 -> "GOOD MORNING"
            hour < 17 -> "GOOD AFTERNOON"
            else -> "GOOD EVENING"
        }
        val is24 = DateFormat.is24HourFormat(context)
        clock.text = SimpleDateFormat(if (is24) "H:mm" else "h:mm", Locale.getDefault()).format(now.time)
        ampm.text = if (is24) "" else if (hour < 12) "AM" else "PM"
        date.text = SimpleDateFormat("EEEE  d MMMM", Locale.getDefault()).format(now.time).uppercase(Locale.getDefault())
        network.text = "◉  " + Network.name(context)
    }

    private fun pill(glyph: String, label: String): TextView {
        val background = roundRect(0x66000000, context.dp(15).toFloat(), context.dp(1), 0x33FFFFFF)
        return context.text(11f, "sans-serif-medium", 0xE6FFFFFF.toInt()).apply {
            text = "$glyph  $label"
            gravity = Gravity.CENTER
            setPadding(context.dp(14), 0, context.dp(14), 0)
            isFocusable = true
            id = View.generateViewId()
            this.background = background
            setOnFocusChangeListener { v, has ->
                background.setColor(if (has) 0xF2FFFFFF.toInt() else 0x66000000)
                setTextColor(if (has) 0xFF111111.toInt() else 0xE6FFFFFF.toInt())
                val scale = if (has) 1.08f else 1f
                v.animate().scaleX(scale).scaleY(scale).setInterpolator(OvershootInterpolator(2f)).setDuration(220).start()
                if (has) onPillFocused(v)
            }
        }
    }
}
