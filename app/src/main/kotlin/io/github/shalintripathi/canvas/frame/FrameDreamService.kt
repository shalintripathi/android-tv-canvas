package io.github.shalintripathi.canvas.frame

import android.service.dreams.DreamService
import android.util.Log
import android.view.KeyEvent
import io.github.shalintripathi.canvas.canvas

/** The screensaver: Left and Right flip photos, volume keys pass through, anything else wakes the TV. */
class FrameDreamService : DreamService() {
    private var slideshow: SlideshowView? = null

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        isInteractive = true
        isFullscreen = true
        isScreenBright = true
        val prefs = canvas().prefs
        val view = SlideshowView(
            this,
            SlideshowView.Config(holdMs = prefs.slideSeconds * 1000L, kenBurns = true, overlay = true, showClock = prefs.showClock),
        )
        slideshow = view
        setContentView(view)
    }

    override fun onDreamingStarted() {
        super.onDreamingStarted()
        slideshow?.start()
    }

    override fun onDreamingStopped() {
        slideshow?.stop()
        super.onDreamingStopped()
    }

    override fun onDetachedFromWindow() {
        slideshow?.release()
        slideshow = null
        super.onDetachedFromWindow()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        when (event.keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT -> {
                if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) slideshow?.previous()
                return true
            }
            KeyEvent.KEYCODE_DPAD_RIGHT -> {
                if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) slideshow?.next()
                return true
            }
            KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN, KeyEvent.KEYCODE_VOLUME_MUTE ->
                return super.dispatchKeyEvent(event)
        }
        Log.i("CanvasDream", "finish on key ${event.keyCode} action ${event.action}")
        finish()
        return true
    }
}
