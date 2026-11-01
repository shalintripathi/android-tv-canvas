package io.github.shalintripathi.canvas.settings

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import io.github.shalintripathi.canvas.canvas

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val app = context.canvas()
        app.system.repair(app.prefs)
    }
}
