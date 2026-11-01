package io.github.shalintripathi.canvas.library

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.os.Handler
import android.os.Looper
import io.github.shalintripathi.canvas.core.ImageMath
import io.github.shalintripathi.canvas.core.LibraryJson
import io.github.shalintripathi.canvas.core.PhotoEntry
import io.github.shalintripathi.canvas.util.writeAtomically
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.text.ParseException
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList

/** Photos added from phones: upright JPEGs in files/photos, thumbnails in files/thumbs, index in library.json. */
class PhotoLibrary(private val context: Context) {

    class UnsupportedImageException(message: String) : Exception(message)

    private val photos = File(context.filesDir, "photos").apply { mkdirs() }
    private val thumbs = File(context.filesDir, "thumbs").apply { mkdirs() }
    private val index = File(context.filesDir, "library.json")
    private val importLock = Any()
    private val listeners = CopyOnWriteArrayList<() -> Unit>()
    private val main = Handler(Looper.getMainLooper())

    @Volatile
    private var entries: List<PhotoEntry> = load()

    fun all(): List<PhotoEntry> = entries

    fun get(id: String): PhotoEntry? = entries.firstOrNull { it.id == id }

    fun photoFile(id: String) = File(photos, "$id.jpg")

    fun thumbFile(id: String) = File(thumbs, "$id.jpg")

    fun addListener(listener: () -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: () -> Unit) {
        listeners.remove(listener)
    }

    /** Stores one uploaded image. A body shorter than [length] means the upload broke off. */
    fun add(body: InputStream, length: Long, takenAtHint: Long?): PhotoEntry {
        val upload = File.createTempFile("upload", ".tmp", context.cacheDir)
        try {
            val copied = upload.outputStream().use { body.copyTo(it) }
            if (copied != length) throw IOException("Upload interrupted")
            // One import at a time: each one briefly holds a few full-size bitmaps.
            return synchronized(importLock) { import(upload, takenAtHint) }
        } finally {
            upload.delete()
        }
    }

    fun delete(id: String): Boolean {
        if (get(id) == null) return false
        update { list -> list.filter { it.id != id } }
        photoFile(id).delete()
        thumbFile(id).delete()
        return true
    }

    fun setFavorite(id: String, favorite: Boolean): Boolean {
        if (get(id) == null) return false
        update { list -> list.map { if (it.id == id) it.copy(favorite = favorite) else it } }
        return true
    }

    private fun import(file: File, takenAtHint: Long?): PhotoEntry {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw UnsupportedImageException(UNSUPPORTED)

        // Malformed EXIF can throw runtime exceptions on API 24-27, not just IOException: import without it.
        val exif = try {
            ExifInterface(file.path)
        } catch (e: Exception) {
            null
        }
        val orientation = exif?.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
            ?: ExifInterface.ORIENTATION_NORMAL
        val takenAt = exif?.let { exifDate(it) } ?: takenAtHint?.takeIf { it > 0 }
        val sample = ImageMath.sampleForLongSide(bounds.outWidth, bounds.outHeight, MAX_SIDE)

        val id = LibraryJson.newId()
        var stored = false
        try {
            val size = try {
                storeJpegs(file, sample, orientation, id)
            } catch (e: OutOfMemoryError) {
                throw UnsupportedImageException(TOO_LARGE)
            }
            val entry = PhotoEntry(id, System.currentTimeMillis(), takenAt, size.first, size.second, false, "phone")
            update { it + entry }
            stored = true
            return entry
        } finally {
            // A failure after the photo was written must not leave its files behind.
            if (!stored) {
                photoFile(id).delete()
                thumbFile(id).delete()
            }
        }
    }

    /** Decodes, scales and rotates the upload, then writes the photo and its thumbnail. Returns the stored size. */
    private fun storeJpegs(file: File, sample: Int, orientation: Int, id: String): Pair<Int, Int> {
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        var current = BitmapFactory.decodeFile(file.path, options) ?: throw UnsupportedImageException(UNSUPPORTED)
        try {
            current = replace(current, scaled(current, MAX_SIDE))
            current = replace(current, upright(current, orientation))
            writeJpeg(current, photoFile(id), 88)
            val thumb = scaled(current, THUMB_SIDE)
            try {
                writeJpeg(thumb, thumbFile(id), 80)
            } finally {
                if (thumb !== current) thumb.recycle()
            }
            return current.width to current.height
        } finally {
            current.recycle()
        }
    }

    /** Frees [old] once [next] has taken its place. [scaled] and [upright] can hand back their input unchanged. */
    private fun replace(old: Bitmap, next: Bitmap): Bitmap {
        if (next !== old) old.recycle()
        return next
    }

    private fun scaled(bitmap: Bitmap, maxSide: Int): Bitmap {
        val (w, h) = ImageMath.fitLongSide(bitmap.width, bitmap.height, maxSide)
        return if (w == bitmap.width && h == bitmap.height) bitmap else Bitmap.createScaledBitmap(bitmap, w, h, true)
    }

    /** Applies the EXIF orientation so the stored JPEG is the right way up. */
    private fun upright(bitmap: Bitmap, orientation: Int): Bitmap {
        val m = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.setScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> m.setRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> {
                m.setRotate(180f)
                m.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_TRANSPOSE -> {
                m.setRotate(90f)
                m.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_90 -> m.setRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> {
                m.setRotate(-90f)
                m.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_270 -> m.setRotate(-90f)
            else -> return bitmap
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, m, true)
    }

    private fun writeJpeg(bitmap: Bitmap, target: File, quality: Int) {
        val tmp = File(target.parentFile, target.name + ".tmp")
        try {
            FileOutputStream(tmp).use {
                if (!bitmap.compress(Bitmap.CompressFormat.JPEG, quality, it)) throw IOException("JPEG encode failed")
            }
            if (!tmp.renameTo(target)) throw IOException("Could not save photo")
        } finally {
            // Gone already after a successful rename; otherwise don't leave a stray .tmp.
            tmp.delete()
        }
    }

    private fun exifDate(exif: ExifInterface): Long? {
        val raw = exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL)
            ?: exif.getAttribute(ExifInterface.TAG_DATETIME) ?: return null
        if (raw.startsWith("0000")) return null
        return try {
            SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).parse(raw)?.time
        } catch (e: ParseException) {
            null
        }
    }

    private fun update(change: (List<PhotoEntry>) -> List<PhotoEntry>) {
        synchronized(this) {
            // Write first: if it throws, memory still matches the index on disk and no listener is told.
            val next = change(entries)
            index.writeAtomically(LibraryJson.encode(next))
            entries = next
        }
        main.post { listeners.forEach { it() } }
    }

    private fun load(): List<PhotoEntry> =
        if (!index.exists()) emptyList() else LibraryJson.decode(index.readText()).filter { photoFile(it.id).exists() }

    companion object {
        const val MAX_SIDE = 1920
        const val THUMB_SIDE = 320
        private const val UNSUPPORTED = "This TV can't read that file. Try a JPEG or PNG photo."
        private const val TOO_LARGE = "This photo is too large for the TV to process. Try a smaller one."
    }
}
