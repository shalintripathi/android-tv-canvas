package io.github.shalintripathi.canvas.phone

import android.app.Activity
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import io.github.shalintripathi.canvas.CanvasApp
import io.github.shalintripathi.canvas.canvas
import io.github.shalintripathi.canvas.core.Pairing
import io.github.shalintripathi.canvas.ui.MATCH
import io.github.shalintripathi.canvas.ui.WRAP
import io.github.shalintripathi.canvas.ui.dp
import io.github.shalintripathi.canvas.ui.frameParams
import io.github.shalintripathi.canvas.ui.linearParams
import io.github.shalintripathi.canvas.ui.roundRect
import io.github.shalintripathi.canvas.ui.text
import io.github.shalintripathi.canvas.util.Network

/** Shows a QR code and a PIN so a phone can open the phone page and pair. */
class PhoneActivity : Activity() {
    private lateinit var app: CanvasApp
    private lateinit var qr: ImageView
    private lateinit var address: TextView
    private lateinit var pin: TextView
    private lateinit var status: TextView
    private val main = Handler(Looper.getMainLooper())
    private var countAtOpen = 0
    private val refreshCode = Runnable { showCode() }
    private val onLibraryChanged: () -> Unit = { updateStatus() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = canvas()
        setContentView(buildUi())
    }

    override fun onResume() {
        super.onResume()
        countAtOpen = app.library.all().size
        app.library.addListener(onLibraryChanged)
        showCode()
        updateStatus()
    }

    override fun onPause() {
        super.onPause()
        main.removeCallbacks(refreshCode)
        app.library.removeListener(onLibraryChanged)
    }

    private fun buildUi(): View {
        val root = FrameLayout(this)
        root.background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(0xFF1B2433.toInt(), 0xFF0E1015.toInt()))
        val row = LinearLayout(this)
        row.gravity = Gravity.CENTER_VERTICAL

        val left = LinearLayout(this)
        left.orientation = LinearLayout.VERTICAL
        left.addView(label("PHONE"))
        left.addView(text(36f, "sans-serif-light", Color.WHITE).apply { text = "Add photos from your phone" },
            linearParams(WRAP, WRAP, 0, dp(6), 0, dp(24)))
        for (step in STEPS) {
            left.addView(text(15f, "sans-serif", 0xDDFFFFFF.toInt()).apply { text = step }, linearParams(WRAP, WRAP, 0, 0, dp(32), dp(12)))
        }
        status = text(15f, "sans-serif-medium", 0xFF8BE0A4.toInt())
        left.addView(status, linearParams(WRAP, WRAP, 0, dp(8), 0, 0))
        left.addView(text(12f, "sans-serif", 0x99FFFFFF.toInt()).apply {
            text = "Your phone and the TV must be on the same Wi-Fi. Photos stay on this TV."
        }, linearParams(WRAP, WRAP, 0, dp(20), 0, 0))
        row.addView(left, LinearLayout.LayoutParams(0, WRAP, 1f))

        val right = LinearLayout(this)
        right.orientation = LinearLayout.VERTICAL
        right.gravity = Gravity.CENTER_HORIZONTAL
        qr = ImageView(this)
        qr.background = roundRect(Color.WHITE, dp(16).toFloat())
        qr.setPadding(dp(14), dp(14), dp(14), dp(14))
        right.addView(qr, LinearLayout.LayoutParams(dp(260), dp(260)))
        right.addView(label("OR OPEN"), linearParams(WRAP, WRAP, 0, dp(18), 0, dp(2)))
        address = text(18f, "sans-serif-medium", Color.WHITE)
        right.addView(address, LinearLayout.LayoutParams(WRAP, WRAP))
        right.addView(label("AND ENTER"), linearParams(WRAP, WRAP, 0, dp(10), 0, dp(2)))
        pin = text(34f, "sans-serif-light", Color.WHITE)
        pin.letterSpacing = 0.12f
        right.addView(pin, LinearLayout.LayoutParams(WRAP, WRAP))
        row.addView(right, LinearLayout.LayoutParams(WRAP, WRAP))

        root.addView(row, frameParams(MATCH, WRAP, Gravity.CENTER_VERTICAL, dp(96), 0, dp(96), 0))
        return root
    }

    private fun label(s: String) = text(11f, "sans-serif-medium", 0x99FFFFFF.toInt()).apply {
        text = s
        letterSpacing = 0.22f
    }

    private fun showCode() {
        main.removeCallbacks(refreshCode)
        val ip = Network.localIpv4()
        val error = app.server.error
        if (ip == null || error != null) {
            qr.setImageDrawable(null)
            address.text = if (ip == null) "Connect the TV to Wi-Fi first" else "Phone page unavailable: $error"
            pin.text = ""
            main.postDelayed(refreshCode, 5_000)
            return
        }
        val code = app.server.pairing.newCode()
        val base = "http://$ip:${CompanionServer.PORT}"
        qr.setImageBitmap(Qr.encode("$base/pair?t=${code.token}", dp(232)))
        address.text = "$ip:${CompanionServer.PORT}"
        pin.text = code.pin.substring(0, 3) + " " + code.pin.substring(3)
        main.postDelayed(refreshCode, Pairing.CODE_TTL_MS - 15_000)
    }

    private fun updateStatus() {
        val added = app.library.all().size - countAtOpen
        status.text = when {
            added <= 0 -> ""
            added == 1 -> "✓ 1 photo added"
            else -> "✓ $added photos added"
        }
    }

    private companion object {
        val STEPS = listOf(
            "1   Scan the code with your phone's camera.",
            "2   Tap Add photos and pick from your gallery. On Android, Google Photos is in the picker.",
            "3   They show up here, on the home screen and in the screensaver.",
        )
    }
}
