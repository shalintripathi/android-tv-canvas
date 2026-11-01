package io.github.shalintripathi.canvas.core

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.security.SecureRandom

/** One photo stored on the TV. [width] and [height] are of the stored, upright JPEG. */
data class PhotoEntry(
    val id: String,
    val addedAt: Long,
    val takenAt: Long?,
    val width: Int,
    val height: Int,
    val favorite: Boolean = false,
    val source: String = "phone",
) {
    val portrait: Boolean get() = height > width

    fun toRef() = PhotoRef(id, portrait, favorite)
}

object LibraryJson {
    private val ID = Regex("[a-z0-9]{12}")
    private const val ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
    private val random = SecureRandom()

    /** Ids name files on disk, so anything else is rejected. */
    fun isValidId(id: String) = ID.matches(id)

    fun newId(): String = String(CharArray(12) { ALPHABET[random.nextInt(ALPHABET.length)] })

    fun toJson(e: PhotoEntry): JSONObject = JSONObject()
        .put("id", e.id)
        .put("addedAt", e.addedAt)
        .put("takenAt", e.takenAt ?: JSONObject.NULL)
        .put("width", e.width)
        .put("height", e.height)
        .put("favorite", e.favorite)
        .put("source", e.source)

    fun encode(entries: List<PhotoEntry>): String = JSONArray().apply { entries.forEach { put(toJson(it)) } }.toString()

    fun decode(text: String): List<PhotoEntry> = try {
        val array = JSONArray(text)
        (0 until array.length()).mapNotNull { i ->
            val o = array.optJSONObject(i) ?: return@mapNotNull null
            val id = o.optString("id")
            val width = o.optInt("width")
            val height = o.optInt("height")
            if (!isValidId(id) || width <= 0 || height <= 0) return@mapNotNull null
            PhotoEntry(
                id = id,
                addedAt = o.optLong("addedAt"),
                takenAt = if (o.isNull("takenAt")) null else o.optLong("takenAt"),
                width = width,
                height = height,
                favorite = o.optBoolean("favorite"),
                source = o.optString("source", "phone"),
            )
        }
    } catch (e: JSONException) {
        emptyList()
    }
}

object PhonesJson {
    fun encode(phones: List<Phone>): String = JSONArray().apply {
        phones.forEach { put(JSONObject().put("hash", it.hash).put("label", it.label).put("pairedAt", it.pairedAt)) }
    }.toString()

    fun decode(text: String): List<Phone> = try {
        val array = JSONArray(text)
        (0 until array.length()).mapNotNull { i ->
            val o = array.optJSONObject(i) ?: return@mapNotNull null
            val hash = o.optString("hash")
            if (hash.length != 64) null else Phone(hash, o.optString("label", "Browser"), o.optLong("pairedAt"))
        }
    } catch (e: JSONException) {
        emptyList()
    }
}
