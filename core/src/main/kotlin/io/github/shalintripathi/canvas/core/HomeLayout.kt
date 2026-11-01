package io.github.shalintripathi.canvas.core

/** The owner's dock order and hidden apps, by package name. */
data class HomeLayout(val dock: List<String>, val hidden: Set<String>) {

    fun moveLeft(pkg: String) = move(pkg, -1)

    fun moveRight(pkg: String) = move(pkg, 1)

    fun addToDock(pkg: String): HomeLayout =
        if (pkg in dock || dock.size >= MAX_DOCK) this else copy(dock = dock + pkg, hidden = hidden - pkg)

    fun removeFromDock(pkg: String) = copy(dock = dock - pkg)

    fun hide(pkg: String) = copy(dock = dock - pkg, hidden = hidden + pkg)

    fun unhide(pkg: String) = copy(hidden = hidden - pkg)

    /** Splits installed apps (already in grid order) into the dock, in dock order, and the grid. */
    fun arrange(installed: List<String>): Pair<List<String>, List<String>> {
        val present = installed.toSet()
        val dockApps = dock.filter { it in present && it !in hidden }
        val inDock = dockApps.toSet()
        return dockApps to installed.filter { it !in inDock && it !in hidden }
    }

    private fun move(pkg: String, delta: Int): HomeLayout {
        val i = dock.indexOf(pkg)
        val j = i + delta
        if (i < 0 || j !in dock.indices) return this
        val d = dock.toMutableList()
        d[i] = d[j].also { d[j] = d[i] }
        return copy(dock = d)
    }

    companion object {
        const val MAX_DOCK = 7

        val PREFERRED = listOf(
            "com.google.android.youtube.tv",
            "com.netflix.ninja",
            "com.amazon.amazonvideo.livingroom",
            "in.startv.hotstar",
            "com.apple.atve.androidtv.appletv",
        )

        fun defaults(installed: Collection<String>) = HomeLayout(PREFERRED.filter { it in installed }, emptySet())
    }
}
