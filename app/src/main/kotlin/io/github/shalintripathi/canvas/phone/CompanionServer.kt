package io.github.shalintripathi.canvas.phone

import io.github.shalintripathi.canvas.CanvasApp
import io.github.shalintripathi.canvas.core.Pairing
import io.github.shalintripathi.canvas.core.http.HttpServer
import java.io.File
import java.io.IOException

/** The phone page: runs while Canvas's process is alive (home screen or screensaver showing). */
class CompanionServer(app: CanvasApp) {
    val pairing = Pairing(FilePhoneStore(File(app.filesDir, "phones.json")))
    private val server = HttpServer(PORT, MAX_UPLOAD, ApiRouter(app, pairing))

    @Volatile
    var error: String? = null
        private set

    fun start() {
        Thread({
            error = try {
                server.start()
                null
            } catch (e: IOException) {
                e.message ?: "port $PORT is busy"
            }
        }, "canvas-http-start").start()
    }

    companion object {
        const val PORT = 8765
        const val MAX_UPLOAD = 40L * 1024 * 1024
    }
}
