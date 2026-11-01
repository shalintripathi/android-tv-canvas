package io.github.shalintripathi.canvas.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class SlidePlannerTest {
    private fun landscape(id: String, favorite: Boolean = false) = PhotoRef(id, portrait = false, favorite = favorite)
    private fun portrait(id: String) = PhotoRef(id, portrait = true)

    @Test
    fun emptyPlannerHasNoSlides() {
        assertNull(SlidePlanner(Random(1)).next())
    }

    @Test
    fun everyPhotoShowsOnceBeforeAnyRepeats() {
        val planner = SlidePlanner(Random(7))
        planner.setPhotos((1..6).map { landscape("p$it") })
        val firstRound = (1..6).map { (planner.next() as Slide.Single).photo.id }
        assertEquals((1..6).map { "p$it" }.toSet(), firstRound.toSet())
    }

    @Test
    fun favouritesShowTwicePerRound() {
        val planner = SlidePlanner(Random(3))
        planner.setPhotos(listOf(landscape("a", favorite = true), landscape("b"), landscape("c")))
        val round = (1..4).map { (planner.next() as Slide.Single).photo.id }
        assertEquals(2, round.count { it == "a" })
        assertEquals(1, round.count { it == "b" })
        assertEquals(1, round.count { it == "c" })
        for (i in 1 until round.size) assertTrue("no back-to-back repeats: $round", round[i] != round[i - 1])
    }

    @Test
    fun twoPortraitsShareTheScreen() {
        val planner = SlidePlanner(Random(5))
        planner.setPhotos(listOf(portrait("p1"), portrait("p2"), landscape("l1")))
        val slides = listOf(planner.next()!!, planner.next()!!)
        val duo = slides.filterIsInstance<Slide.Duo>().single()
        assertEquals(setOf("p1", "p2"), duo.photos.map { it.id }.toSet())
        assertEquals("l1", slides.filterIsInstance<Slide.Single>().single().photo.id)
    }

    @Test
    fun aLonePortraitShowsOnItsOwn() {
        val planner = SlidePlanner(Random(2))
        planner.setPhotos(listOf(portrait("p1")))
        assertEquals("p1", (planner.next() as Slide.Single).photo.id)
    }

    @Test
    fun previousAndNextWalkTheHistory() {
        val planner = SlidePlanner(Random(9))
        planner.setPhotos((1..5).map { landscape("p$it") })
        val a = planner.next()
        val b = planner.next()
        val c = planner.next()
        assertEquals(b, planner.previous())
        assertEquals(a, planner.previous())
        assertNull(planner.previous())
        assertEquals(b, planner.next())
        assertEquals(c, planner.next())
    }

    @Test
    fun newPhotosShowNext() {
        val planner = SlidePlanner(Random(4))
        planner.setPhotos((1..5).map { landscape("old$it") })
        planner.next()
        planner.setPhotos((1..5).map { landscape("old$it") } + landscape("new"))
        assertEquals("new", (planner.next() as Slide.Single).photo.id)
    }

    @Test
    fun removedPhotosNeverReturn() {
        val planner = SlidePlanner(Random(11))
        planner.setPhotos((1..4).map { landscape("p$it") })
        planner.next()
        planner.setPhotos(listOf(landscape("p1"), landscape("p2")))
        repeat(10) {
            val id = (planner.next() as Slide.Single).photo.id
            assertTrue(id == "p1" || id == "p2")
        }
    }
}
