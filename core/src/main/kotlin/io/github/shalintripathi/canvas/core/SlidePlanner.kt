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
 * A photo never shows on two consecutive slides while the library has another photo to show instead.
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
        // Ids on the slide just shown: nothing on it is drawn again straight away.
        val last = history.lastOrNull()?.photos?.map { it.id }.orEmpty()
        // An empty bag, or one holding only copies of that slide, takes a fresh round at the end.
        if (bag.isEmpty() || bag.all { it.id in last }) refill()
        // Leftovers stay in front; the first copy not on the last slide goes next.
        // The coerceAtLeast fallback only matters when the library has nothing else to show.
        val first = bag.removeAt(bag.indexOfFirst { it.id !in last }.coerceAtLeast(0))
        if (!first.portrait) return Slide.Single(first)
        val partner = bag.indexOfFirst { it.portrait && it.id != first.id && it.id !in last }
        return if (partner < 0) Slide.Single(first) else Slide.Duo(first, bag.removeAt(partner))
    }

    /** Appends one shuffled round to the end of the bag: every photo once, favourites twice. */
    private fun refill() {
        bag.addAll(photos.flatMap { if (it.favorite) listOf(it, it) else listOf(it) }.shuffled(random))
    }

    private companion object {
        const val HISTORY = 50
    }
}
