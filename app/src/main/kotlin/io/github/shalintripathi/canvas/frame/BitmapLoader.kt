package io.github.shalintripathi.canvas.frame

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import io.github.shalintripathi.canvas.canvas
import io.github.shalintripathi.canvas.core.ImageMath
import io.github.shalintripathi.canvas.core.PhotoRef
import java.io.FileInputStream
import java.io.IOException
import java.io.InputStream

object BitmapLoader {
    private const val BUILTIN = "builtin:"

    /** The bundled nature photos (all landscape). */
    fun builtins(context: Context): List<PhotoRef> {
        val names = try {
            context.assets.list("wallpapers")
        } catch (e: IOException) {
            null
        }
        return names.orEmpty().filter { it.endsWith(".jpg") }.sorted().map { PhotoRef(BUILTIN + it, portrait = false) }
    }

    /** Decodes a library or built-in photo just big enough to fill [reqWidth] x [reqHeight]. */
    fun load(context: Context, id: String, reqWidth: Int, reqHeight: Int): Bitmap? = try {
        if (id.startsWith(BUILTIN)) {
            val name = "wallpapers/" + id.removePrefix(BUILTIN)
            decode({ context.assets.open(name) }, reqWidth, reqHeight)
        } else {
            val file = context.canvas().library.photoFile(id)
            if (file.exists()) decode({ FileInputStream(file) }, reqWidth, reqHeight) else null
        }
    } catch (e: IOException) {
        null
    } catch (e: OutOfMemoryError) {
        null
    }

    /** A tiny, softened copy to sit blurred behind a lone portrait. */
    fun backdrop(source: Bitmap): Bitmap {
        val w = 24
        val h = maxOf(1, w * source.height / source.width)
        val tiny = Bitmap.createScaledBitmap(source, w, h, true)
        return Bitmap.createScaledBitmap(tiny, w * 8, h * 8, true)
    }

    private fun decode(open: () -> InputStream, reqWidth: Int, reqHeight: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        open().use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        val options = BitmapFactory.Options().apply {
            inSampleSize = ImageMath.sampleToFill(bounds.outWidth, bounds.outHeight, reqWidth, reqHeight)
        }
        return open().use { BitmapFactory.decodeStream(it, null, options) }
    }
}
