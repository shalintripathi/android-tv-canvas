package io.github.shalintripathi.canvas.ui

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.util.TypedValue
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.roundToInt

const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
const val WRAP = ViewGroup.LayoutParams.WRAP_CONTENT

fun Context.dp(value: Number): Int = (value.toFloat() * resources.displayMetrics.density).roundToInt()

fun View.dp(value: Number): Int = context.dp(value)

fun Context.text(sp: Float, family: String, color: Int): TextView = TextView(this).apply {
    setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
    typeface = Typeface.create(family, Typeface.NORMAL)
    setTextColor(color)
}

fun frameParams(width: Int, height: Int, gravity: Int = -1, left: Int = 0, top: Int = 0, right: Int = 0, bottom: Int = 0) =
    FrameLayout.LayoutParams(width, height, gravity).apply { setMargins(left, top, right, bottom) }

fun linearParams(width: Int, height: Int, left: Int = 0, top: Int = 0, right: Int = 0, bottom: Int = 0) =
    LinearLayout.LayoutParams(width, height).apply { setMargins(left, top, right, bottom) }

fun roundRect(color: Int, radius: Float, strokeWidth: Int = 0, strokeColor: Int = 0) = GradientDrawable().apply {
    cornerRadius = radius
    setColor(color)
    if (strokeWidth > 0) setStroke(strokeWidth, strokeColor)
}

fun withAlpha(color: Int, alpha: Float): Int =
    Color.argb((alpha * 255).roundToInt(), Color.red(color), Color.green(color), Color.blue(color))

fun darken(color: Int, factor: Float): Int =
    Color.rgb((Color.red(color) * factor).toInt(), (Color.green(color) * factor).toInt(), (Color.blue(color) * factor).toInt())

/** Starts an activity. TV app launches carry their own flags; everything else gets NEW_TASK. */
fun Context.launch(intent: Intent): Boolean = try {
    if (!intent.hasCategory(Intent.CATEGORY_LEANBACK_LAUNCHER)) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    startActivity(intent)
    true
} catch (e: RuntimeException) {
    false
}

/** For system broadcasts (packages, time). The flag is required from Android 14 for targetSdk 34+. */
@SuppressLint("UnspecifiedRegisterReceiverFlag")
fun Context.registerReceiverCompat(receiver: BroadcastReceiver, filter: IntentFilter) {
    if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
    else registerReceiver(receiver, filter)
}
