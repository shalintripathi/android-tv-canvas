package io.github.shalintripathi.canvas.core

/** Decode-size maths shared by the importer and the slideshow. */
object ImageMath {
    /** Largest power-of-two sample size that keeps both sides at least the requested size (for centre-crop). */
    fun sampleToFill(width: Int, height: Int, reqWidth: Int, reqHeight: Int): Int {
        if (reqWidth <= 0 || reqHeight <= 0) return 1
        var sample = 1
        while (width / (sample * 2) >= reqWidth && height / (sample * 2) >= reqHeight) sample *= 2
        return sample
    }

    /** Largest power-of-two sample size that keeps the long side at least [maxSide]. */
    fun sampleForLongSide(width: Int, height: Int, maxSide: Int): Int {
        val long = maxOf(width, height)
        var sample = 1
        while (long / (sample * 2) >= maxSide) sample *= 2
        return sample
    }

    /** Size after shrinking so the long side is at most [maxSide]. Never enlarges. */
    fun fitLongSide(width: Int, height: Int, maxSide: Int): Pair<Int, Int> {
        val long = maxOf(width, height)
        if (long <= maxSide) return width to height
        val w = (width.toLong() * maxSide / long).toInt().coerceAtLeast(1)
        val h = (height.toLong() * maxSide / long).toInt().coerceAtLeast(1)
        return w to h
    }
}
