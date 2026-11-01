package io.github.shalintripathi.canvas.core

import org.junit.Assert.assertEquals
import org.junit.Test

class ImageMathTest {
    @Test
    fun sampleToFillKeepsBothSidesAtLeastTheTarget() {
        assertEquals(2, ImageMath.sampleToFill(4000, 3000, 1920, 1080))
        assertEquals(1, ImageMath.sampleToFill(1920, 1080, 1920, 1080))
        assertEquals(4, ImageMath.sampleToFill(8000, 6000, 1920, 1080))
        // A portrait filling a landscape screen is limited by its width.
        assertEquals(1, ImageMath.sampleToFill(3000, 4000, 1920, 1080))
    }

    @Test
    fun sampleToFillIgnoresMissingTargets() {
        assertEquals(1, ImageMath.sampleToFill(4000, 3000, 0, 0))
    }

    @Test
    fun sampleForLongSideKeepsTheLongSideAtLeastMax() {
        assertEquals(2, ImageMath.sampleForLongSide(4032, 3024, 1920))
        assertEquals(4, ImageMath.sampleForLongSide(3024, 8064, 1920))
        assertEquals(1, ImageMath.sampleForLongSide(1200, 800, 1920))
    }

    @Test
    fun fitLongSideShrinksButNeverEnlarges() {
        assertEquals(1920 to 1440, ImageMath.fitLongSide(4032, 3024, 1920))
        assertEquals(1080 to 1920, ImageMath.fitLongSide(2160, 3840, 1920))
        assertEquals(800 to 600, ImageMath.fitLongSide(800, 600, 1920))
    }
}
