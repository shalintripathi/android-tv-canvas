package io.github.shalintripathi.canvas.core

import kotlin.random.Random

/** A photo as the slideshow sees it. [id] is a library id or "builtin:<asset name>". */
data class PhotoRef(val id: String, val portrait: Boolean, val favorite: Boolean = false)

/** What is on screen at once: one photo, or two portraits side by side. */
sealed class Slide {
    abstract val photos: List<PhotoRef>

    data class Single(val photo: PhotoRef) : Slide() {
        override val photos: List<PhotoRef> get() = listOf(photo)
    }

    data class Duo(val left: PhotoRef, val right: PhotoRef) : Slide() {
        override val photos: List<PhotoRef> get() = listOf(left, right)
    }
}

/**
 * Shuffle bag: each photo shows once per round (favourites twice) before anything repeats.
 * Drawing a portrait pulls the next portrait in the bag forward so the two share the screen.
 * Keeps a short history so Left and Right on the remote can step back and forth.
 */
class SlidePlanner(private val random: Random = Random.Default) {
    private var photos: List<PhotoRef> = emptyList()
    private val bag = ArrayDeque<PhotoRef>()
    private val history = ArrayList<Slide>()
    private var cursor = -1

    /**
     * Replaces the photo set. Photos added to an existing set go to the front of the bag so they
     * show next; a wholly different set (built-ins giving way to the library) starts a fresh shuffle.
     */
    fun setPhotos(list: List<PhotoRef>) {
        val before = photos.map { it.id }.toSet()
        photos = list.distinctBy { it.id }
        val byId = photos.associateBy { it.id }
        val kept = bag.mapNotNull { byId[it.id] }
        bag.clear()
        if (photos.any { it.id in before }) bag.addAll(photos.filter { it.id !in before })
        bag.addAll(kept)
        history.removeAll { slide -> slide.photos.any { it.id !in byId } }
        cursor = history.lastIndex
    }

    fun next(): Slide? {
        if (cursor < history.lastIndex) {
            cursor++
            return history[cursor]
        }
        val slide = draw() ?: return null
        history.add(slide)
        if (history.size > HISTORY) history.removeAt(0)
        cursor = history.lastIndex
        return slide
    }

    fun previous(): Slide? {
        if (cursor <= 0) return null
        cursor--
        return history[cursor]
    }

    private fun draw(): Slide? {
        if (photos.isEmpty()) return null
        if (bag.isEmpty()) refill()
        val first = bag.removeFirst()
        if (!first.portrait) return Slide.Single(first)
        val partner = bag.indexOfFirst { it.portrait && it.id != first.id }
        return if (partner < 0) Slide.Single(first) else Slide.Duo(first, bag.removeAt(partner))
    }

    private fun refill() {
        val round = photos.flatMap { if (it.favorite) listOf(it, it) else listOf(it) }.shuffled(random).toMutableList()
        // Don't open a round with what closed the last one.
        val last = history.lastOrNull()?.photos?.map { it.id }.orEmpty()
        if (round.size > 1 && round[0].id in last) {
            val j = round.indexOfFirst { it.id !in last }
            if (j > 0) round[0] = round[j].also { round[j] = round[0] }
        }
        // Keep a favourite's two copies apart: move the second copy to the first spot not next to its twin.
        var i = 1
        while (i < round.size) {
            if (round[i].id != round[i - 1].id) {
                i++
                continue
            }
            val moved = round.removeAt(i)
            val at = (0..round.size).firstOrNull { p ->
                (p == 0 || round[p - 1].id != moved.id) && (p == round.size || round[p].id != moved.id)
            }
            round.add(at ?: i, moved)
            if (at == null) i++
        }
        bag.addAll(round)
    }

    private companion object {
        const val HISTORY = 50
    }
}
