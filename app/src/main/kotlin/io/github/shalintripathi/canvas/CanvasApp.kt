package io.github.shalintripathi.canvas

import android.app.Application
import android.content.Context
import io.github.shalintripathi.canvas.apps.AppRepository
import io.github.shalintripathi.canvas.library.PhotoLibrary
import io.github.shalintripathi.canvas.phone.CompanionServer
import io.github.shalintripathi.canvas.settings.Prefs
import io.github.shalintripathi.canvas.settings.SystemSettings

class CanvasApp : Application() {
    lateinit var prefs: Prefs
        private set
    lateinit var system: SystemSettings
        private set
    lateinit var apps: AppRepository
        private set
    lateinit var library: PhotoLibrary
        private set
    lateinit var server: CompanionServer
        private set

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        system = SystemSettings(this)
        apps = AppRepository(this)
        library = PhotoLibrary(this)
        system.rememberPrevious(prefs)
        server = CompanionServer(this)
        server.start()
    }
}

fun Context.canvas(): CanvasApp = applicationContext as CanvasApp
