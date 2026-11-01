package io.github.shalintripathi.canvas.home

import android.content.Context
import android.graphics.Color
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.TextView
import io.github.shalintripathi.canvas.ui.MATCH
import io.github.shalintripathi.canvas.ui.WRAP
import io.github.shalintripathi.canvas.ui.dp
import io.github.shalintripathi.canvas.ui.roundRect
import io.github.shalintripathi.canvas.ui.text

/** A small D-pad menu shown under (or above) a view. */
object PopupList {
    class Item(val label: String, val action: () -> Unit)

    fun show(anchor: View, items: List<Item>, width: Int, onDismiss: () -> Unit): PopupWindow {
        val context = anchor.context
        val list = LinearLayout(context)
        list.orientation = LinearLayout.VERTICAL
        list.setPadding(context.dp(8), context.dp(8), context.dp(8), context.dp(8))
        list.background = roundRect(0xF02A2A2E.toInt(), context.dp(16).toFloat(), context.dp(1), 0x26FFFFFF)

        val popup = PopupWindow(list, width, WRAP, true)
        val rows = items.map { item ->
            row(context, item.label).apply {
                setOnClickListener {
                    popup.dismiss()
                    item.action()
                }
            }
        }
        rows.forEach { list.addView(it, LinearLayout.LayoutParams(MATCH, context.dp(36))) }
        rows.forEachIndexed { i, r ->
            r.nextFocusUpId = rows[maxOf(0, i - 1)].id
            r.nextFocusDownId = rows[minOf(rows.lastIndex, i + 1)].id
            r.nextFocusLeftId = r.id
            r.nextFocusRightId = r.id
        }
        popup.isOutsideTouchable = true
        popup.elevation = context.dp(16).toFloat()
        popup.setOnDismissListener { onDismiss() }
        // Under the anchor when the whole list fits there; otherwise above it (the dock sits at the screen's bottom).
        val gap = context.dp(8)
        list.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY), View.MeasureSpec.UNSPECIFIED)
        val anchorLocation = IntArray(2)
        anchor.getLocationOnScreen(anchorLocation)
        val fitsBelow = anchorLocation[1] + anchor.height + gap + list.measuredHeight <= context.resources.displayMetrics.heightPixels
        val yOffset = if (fitsBelow) gap else -(anchor.height + gap + list.measuredHeight)
        popup.showAsDropDown(anchor, 0, yOffset, Gravity.START)
        rows.firstOrNull()?.requestFocus()
        return popup
    }

    private fun row(context: Context, label: String): TextView {
        val background = roundRect(Color.TRANSPARENT, context.dp(10).toFloat())
        return context.text(13f, "sans-serif", Color.WHITE).apply {
            text = label
            id = View.generateViewId()
            gravity = Gravity.CENTER_VERTICAL
            setPadding(context.dp(14), 0, context.dp(14), 0)
            isFocusable = true
            this.background = background
            setOnFocusChangeListener { _, has ->
                background.setColor(if (has) 0xF2FFFFFF.toInt() else Color.TRANSPARENT)
                setTextColor(if (has) 0xFF111111.toInt() else Color.WHITE)
            }
        }
    }
}
