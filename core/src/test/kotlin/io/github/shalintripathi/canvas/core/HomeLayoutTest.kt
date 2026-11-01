package io.github.shalintripathi.canvas.core

import org.junit.Assert.assertEquals
import org.junit.Test

class HomeLayoutTest {
    private val layout = HomeLayout(listOf("yt", "nf", "pv"), emptySet())

    @Test
    fun movesWithinTheDockButNotPastTheEnds() {
        assertEquals(listOf("nf", "yt", "pv"), layout.moveLeft("nf").dock)
        assertEquals(listOf("yt", "pv", "nf"), layout.moveRight("nf").dock)
        assertEquals(layout, layout.moveLeft("yt"))
        assertEquals(layout, layout.moveRight("pv"))
        assertEquals(layout, layout.moveLeft("missing"))
    }

    @Test
    fun addsAndRemoves() {
        assertEquals(listOf("yt", "nf", "pv", "hs"), layout.addToDock("hs").dock)
        assertEquals(layout, layout.addToDock("nf"))
        assertEquals(listOf("yt", "pv"), layout.removeFromDock("nf").dock)
    }

    @Test
    fun theDockIsCapped() {
        val full = HomeLayout((1..HomeLayout.MAX_DOCK).map { "app$it" }, emptySet())
        assertEquals(full, full.addToDock("one-more"))
    }

    @Test
    fun hidingTakesAppsOutOfTheDockAndUnhidingBringsThemBack() {
        val hidden = layout.hide("nf")
        assertEquals(listOf("yt", "pv"), hidden.dock)
        assertEquals(setOf("nf"), hidden.hidden)
        assertEquals(emptySet<String>(), hidden.unhide("nf").hidden)
        assertEquals(emptySet<String>(), hidden.addToDock("nf").hidden)
    }

    @Test
    fun arrangeSplitsInstalledAppsIntoDockAndGrid() {
        val installed = listOf("aa", "nf", "pv", "zz", "hidden")
        val (dock, grid) = HomeLayout(listOf("nf", "gone", "pv"), setOf("hidden")).arrange(installed)
        assertEquals(listOf("nf", "pv"), dock)
        assertEquals(listOf("aa", "zz"), grid)
    }

    @Test
    fun defaultsPinPreferredAppsThatAreInstalled() {
        val installed = setOf("com.netflix.ninja", "com.google.android.youtube.tv", "other")
        assertEquals(listOf("com.google.android.youtube.tv", "com.netflix.ninja"), HomeLayout.defaults(installed).dock)
    }
}
