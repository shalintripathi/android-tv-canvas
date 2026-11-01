package io.github.shalintripathi.canvas.phone

import io.github.shalintripathi.canvas.CanvasApp
import io.github.shalintripathi.canvas.core.HomeLayout
import io.github.shalintripathi.canvas.core.LibraryJson
import io.github.shalintripathi.canvas.core.Pairing
import io.github.shalintripathi.canvas.core.http.HttpHandler
import io.github.shalintripathi.canvas.core.http.HttpRequest
import io.github.shalintripathi.canvas.core.http.HttpResponse
import io.github.shalintripathi.canvas.library.PhotoLibrary
import io.github.shalintripathi.canvas.settings.Prefs
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException

/**
 * The phone page's HTTP API. Everything under /api/ needs a paired phone's cookie; anything that
 * changes state also needs the X-Canvas header, which a cross-site page cannot send without CORS.
 */
class ApiRouter(private val app: CanvasApp, private val pairing: Pairing) : HttpHandler {

    override fun handle(request: HttpRequest): HttpResponse {
        val path = request.path
        val method = request.method
        if (method == "GET" && (path == "/" || path == "/index.html")) return page()
        if (method == "GET" && path == "/pair") return pairWithToken(request)
        if (!path.startsWith("/api/")) return HttpResponse.error(404, "Not found")
        if (method != "GET" && request.header("x-canvas") != "1") return HttpResponse.error(403, "Missing X-Canvas header")
        if (method == "POST" && path == "/api/pair") return pairWithPin(request)

        val session = request.cookies[COOKIE]
        if (!pairing.isPaired(session)) return HttpResponse.error(401, "Not paired")
        val seg = path.removePrefix("/api/").split('/')
        return when {
            seg == listOf("photos") && method == "GET" -> listPhotos()
            seg == listOf("photos") && method == "PUT" -> upload(request)
            seg.size == 2 && seg[0] == "photos" && method == "DELETE" -> deletePhoto(seg[1])
            seg.size == 3 && seg[0] == "photos" && seg[2] == "thumb" && method == "GET" -> thumb(seg[1])
            seg.size == 3 && seg[0] == "photos" && seg[2] == "favorite" && method == "PUT" -> favorite(seg[1], request)
            seg == listOf("settings") && method == "GET" -> settings()
            seg == listOf("settings") && method == "PUT" -> updateSettings(request)
            seg == listOf("apps") && method == "GET" -> apps()
            seg.size == 3 && seg[0] == "apps" && seg[2] == "icon" && method == "GET" -> icon(seg[1])
            seg == listOf("home") && method == "PUT" -> updateHome(request)
            seg == listOf("phones") && method == "GET" -> phones(session)
            seg.size == 2 && seg[0] == "phones" && method == "DELETE" -> revokePhone(seg[1])
            else -> HttpResponse.error(404, "Not found")
        }
    }

    private fun page(): HttpResponse =
        HttpResponse.bytes(app.assets.open("web/index.html").use { it.readBytes() }, "text/html; charset=utf-8")

    private fun pairWithToken(r: HttpRequest): HttpResponse {
        val session = pairing.pairWithToken(r.query["t"].orEmpty(), Pairing.deviceLabel(r.header("user-agent")))
            ?: return HttpResponse.redirect("/?expired=1")
        return HttpResponse.redirect("/", mapOf("Set-Cookie" to cookie(session)))
    }

    private fun pairWithPin(r: HttpRequest): HttpResponse {
        val body = json(r) ?: return HttpResponse.error(400, "Bad JSON")
        val pin = body.optString("pin").filter { it.isDigit() }
        return when (val result = pairing.pairWithPin(pin, Pairing.deviceLabel(r.header("user-agent")))) {
            is Pairing.PinResult.Paired -> HttpResponse.json(OK, headers = mapOf("Set-Cookie" to cookie(result.session)))
            is Pairing.PinResult.Wrong -> HttpResponse.error(403, "Wrong code. ${result.attemptsLeft} tries left.")
            Pairing.PinResult.NoCode -> HttpResponse.error(410, "No active code. On the TV, open Phone to get a new one.")
        }
    }

    private fun listPhotos(): HttpResponse {
        val array = JSONArray()
        app.library.all().sortedByDescending { it.addedAt }.forEach { array.put(LibraryJson.toJson(it)) }
        return HttpResponse.json(array.toString())
    }

    private fun upload(r: HttpRequest): HttpResponse {
        if (r.contentLength <= 0) return HttpResponse.error(400, "Empty upload")
        return try {
            val entry = app.library.add(r.body, r.contentLength, r.query["takenAt"]?.toLongOrNull())
            HttpResponse.json(LibraryJson.toJson(entry).toString(), 201)
        } catch (e: PhotoLibrary.UnsupportedImageException) {
            HttpResponse.error(415, e.message ?: "Unsupported image")
        } catch (e: IOException) {
            HttpResponse.error(400, "Upload interrupted")
        }
    }

    private fun deletePhoto(id: String) =
        if (app.library.delete(id)) HttpResponse.json(OK) else HttpResponse.error(404, "No such photo")

    private fun thumb(id: String): HttpResponse {
        if (app.library.get(id) == null) return HttpResponse.error(404, "No such photo")
        val file = app.library.thumbFile(id)
        return if (file.exists()) HttpResponse.file(file, "image/jpeg", CACHE) else HttpResponse.error(404, "No such photo")
    }

    private fun favorite(id: String, r: HttpRequest): HttpResponse {
        val body = json(r) ?: return HttpResponse.error(400, "Bad JSON")
        return if (app.library.setFavorite(id, body.optBoolean("favorite"))) HttpResponse.json(OK)
        else HttpResponse.error(404, "No such photo")
    }

    private fun settings(): HttpResponse {
        val system = app.system
        val prefs = app.prefs
        val screensaver = JSONObject()
            .put("useCanvas", prefs.useCanvasScreensaver)
            .put("active", system.isCanvasScreensaver() ?: prefs.useCanvasScreensaver)
            .put("canChange", system.canChange())
            .put("startAfterMin", system.startAfterMinutes())
            .put("turnOffAfterMin", system.turnOffAfterMinutes(prefs))
            .put("slideSeconds", prefs.slideSeconds)
            .put("showClock", prefs.showClock)
        val home = JSONObject().put("wallpaper", prefs.wallpaper)
        return HttpResponse.json(JSONObject().put("screensaver", screensaver).put("home", home).toString())
    }

    private fun updateSettings(r: HttpRequest): HttpResponse {
        val body = json(r) ?: return HttpResponse.error(400, "Bad JSON")
        val system = app.system
        val prefs = app.prefs
        val s = body.optJSONObject("screensaver")
        if (s != null) {
            if (s.has("useCanvas")) {
                val on = s.optBoolean("useCanvas")
                prefs.useCanvasScreensaver = on
                val applied = if (on) system.useCanvas() else system.restorePrevious(prefs)
                if (!applied) return HttpResponse.error(409, "The TV didn't allow this change. Run tools/canvas install again.")
            }
            if (s.has("startAfterMin")) {
                val minutes = s.optInt("startAfterMin")
                if (minutes !in START_AFTER) return HttpResponse.error(400, "Pick 5, 10, 15 or 30 minutes")
                if (!system.setStartAfterMinutes(minutes)) return HttpResponse.error(409, "The TV didn't allow this change.")
            }
            if (s.has("turnOffAfterMin")) {
                val minutes = s.optInt("turnOffAfterMin")
                if (minutes !in TURN_OFF) return HttpResponse.error(400, "Pick 15, 30, 60 or 240 minutes, or never")
                if (!system.setTurnOffAfterMinutes(minutes, prefs)) return HttpResponse.error(409, "The TV didn't allow this change.")
            }
            if (s.has("slideSeconds")) prefs.slideSeconds = s.optInt("slideSeconds")
            if (s.has("showClock")) prefs.showClock = s.optBoolean("showClock")
        }
        val wallpaper = body.optJSONObject("home")?.optString("wallpaper").orEmpty()
        if (wallpaper == Prefs.WALLPAPER_PHOTOS || wallpaper == Prefs.WALLPAPER_BUILTIN) prefs.wallpaper = wallpaper
        return settings()
    }

    private fun apps(): HttpResponse {
        val repo = app.apps
        val labels = repo.labels(repo.excludedPackages())
        val layout = app.prefs.layout(labels.map { it.first })
        val array = JSONArray()
        for ((pkg, label) in labels) {
            array.put(
                JSONObject().put("pkg", pkg).put("label", label)
                    .put("dock", layout.dock.indexOf(pkg)).put("hidden", pkg in layout.hidden),
            )
        }
        return HttpResponse.json(array.toString())
    }

    private fun icon(pkg: String): HttpResponse {
        val png = app.apps.artPng(pkg) ?: return HttpResponse.error(404, "No such app")
        return HttpResponse.bytes(png, "image/png", CACHE)
    }

    private fun updateHome(r: HttpRequest): HttpResponse {
        val body = json(r) ?: return HttpResponse.error(400, "Bad JSON")
        val dock = strings(body.optJSONArray("dock")).distinct().take(HomeLayout.MAX_DOCK)
        val hidden = strings(body.optJSONArray("hidden")).toSet()
        app.prefs.saveLayout(HomeLayout(dock, hidden))
        return apps()
    }

    private fun phones(session: String?): HttpResponse {
        val mine = pairing.phoneFor(session)?.id
        val array = JSONArray()
        pairing.phones().sortedByDescending { it.pairedAt }.forEach {
            array.put(JSONObject().put("id", it.id).put("label", it.label).put("pairedAt", it.pairedAt).put("current", it.id == mine))
        }
        return HttpResponse.json(array.toString())
    }

    private fun revokePhone(id: String) =
        if (pairing.revoke(id)) HttpResponse.json(OK) else HttpResponse.error(404, "No such phone")

    private fun json(r: HttpRequest): JSONObject? = try {
        JSONObject(r.bodyText())
    } catch (e: JSONException) {
        null
    }

    private fun strings(array: JSONArray?): List<String> =
        if (array == null) emptyList() else (0 until array.length()).mapNotNull { array.optString(it).takeIf { s -> s.isNotBlank() } }

    companion object {
        const val COOKIE = "canvas_session"
        private const val OK = "{\"ok\":true}"
        private val CACHE = mapOf("Cache-Control" to "private, max-age=86400")
        private val START_AFTER = setOf(5, 10, 15, 30)
        private val TURN_OFF = setOf(15, 30, 60, 240, -1)

        fun cookie(session: String) = "$COOKIE=$session; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
    }
}
