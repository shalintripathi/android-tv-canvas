package io.github.shalintripathi.canvas.home

import android.content.Context
import android.graphics.Rect
import android.widget.FrameLayout
import android.widget.ScrollView

/** 16:9 container for app banners. */
class AspectFrame(context: Context) : FrameLayout(context) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(width * 9 / 16, MeasureSpec.EXACTLY))
    }
}

/** ScrollView that never auto-scrolls to the focused child; the home screen scrolls it by row. */
class HomeScrollView(context: Context) : ScrollView(context) {
    override fun computeScrollDeltaToGetChildRectOnScreen(rect: Rect): Int = 0
}
