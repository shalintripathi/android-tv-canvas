package io.github.shalintripathi.canvas.home

import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.graphics.Color
import android.graphics.Outline
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewOutlineProvider
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.TextView
import io.github.shalintripathi.canvas.CanvasApp
import io.github.shalintripathi.canvas.apps.AppInfo
import io.github.shalintripathi.canvas.apps.AppRepository
import io.github.shalintripathi.canvas.canvas
import io.github.shalintripathi.canvas.core.HomeLayout
import io.github.shalintripathi.canvas.frame.SlideshowView
import io.github.shalintripathi.canvas.phone.PhoneActivity
import io.github.shalintripathi.canvas.settings.Prefs
import io.github.shalintripathi.canvas.ui.MATCH
import io.github.shalintripathi.canvas.ui.WRAP
import io.github.shalintripathi.canvas.ui.darken
import io.github.shalintripathi.canvas.ui.dp
import io.github.shalintripathi.canvas.ui.frameParams
import io.github.shalintripathi.canvas.ui.launch
import io.github.shalintripathi.canvas.ui.linearParams
import io.github.shalintripathi.canvas.ui.registerReceiverCompat
import io.github.shalintripathi.canvas.ui.roundRect
import io.github.shalintripathi.canvas.ui.text
import io.github.shalintripathi.canvas.ui.withAlpha

/**
 * The home screen: clock and pills on top, the focused dock app's name over a glow coloured from its
 * banner, the dock, and every other app in a grid below. The wallpaper is a slow [SlideshowView].
 */
class HomeActivity : Activity() {

    private lateinit var app: CanvasApp
    private val dockCards = ArrayList<View>()
    private val gridCards = ArrayList<View>()

    private lateinit var header: Header
    private lateinit var scroll: HomeScrollView
    private lateinit var content: LinearLayout
    private lateinit var hero: FrameLayout
    private lateinit var dockRow: LinearLayout
    private lateinit var grid: LinearLayout
    private lateinit var title: TextView
    private lateinit var appsLabel: TextView
    private lateinit var glow: View
    private lateinit var gridShade: View
    private lateinit var wallpaper: SlideshowView

    private var layout = HomeLayout(emptyList(), emptySet())
    private var glowColor = AppRepository.DEFAULT_COLOR
    private var glowAnim: ValueAnimator? = null
    private var lastFocusedPkg: String? = null
    private var lastFocusedPill = View.NO_ID
    private var rebuilding = false
    private var firstShow = true
    private var popup: PopupWindow? = null

    private val packageReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = buildApps()
    }
    private val tickReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = header.update()
    }
    private val prefsListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == Prefs.DOCK || key == Prefs.HIDDEN) buildApps()
        if (key == Prefs.WALLPAPER) wallpaper.builtinOnly = app.prefs.wallpaper == Prefs.WALLPAPER_BUILTIN
    }

    // ---------------------------------------------------------------- lifecycle

    @Suppress("DEPRECATION")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = canvas()
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        setContentView(buildUi())
        buildApps()
        registerReceiverCompat(packageReceiver, IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_CHANGED)
            addDataScheme("package")
        })
        app.prefs.register(prefsListener)
    }

    override fun onResume() {
        super.onResume()
        registerReceiverCompat(tickReceiver, IntentFilter(Intent.ACTION_TIME_TICK).apply {
            addAction(Intent.ACTION_TIME_CHANGED)
            addAction(Intent.ACTION_TIMEZONE_CHANGED)
        })
        header.update()
        // Catch installs and removals that happened while another app was in front.
        if (app.apps.hasChanges()) buildApps() else restoreFocus()
        if (firstShow) {
            firstShow = false
            playEntrance()
        }
        wallpaper.start()
        Thread { app.system.repair(app.prefs) }.start()
    }

    override fun onPause() {
        super.onPause()
        unregisterReceiver(tickReceiver)
        wallpaper.stop()
        popup?.dismiss()
    }

    override fun onDestroy() {
        unregisterReceiver(packageReceiver)
        app.prefs.unregister(prefsListener)
        wallpaper.release()
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        // Read this before super: Home arrives here from any app, but only "Home pressed while already home"
        // (this window has focus and was not just brought forward) goes back to the first dock app.
        val alreadyOnHome = hasWindowFocus() && (intent.flags and Intent.FLAG_ACTIVITY_BROUGHT_TO_FRONT) == 0
        super.onNewIntent(intent)
        popup?.dismiss()
        if (alreadyOnHome) {
            lastFocusedPkg = null
            lastFocusedPill = View.NO_ID
            (dockCards.firstOrNull() ?: gridCards.firstOrNull())?.requestFocus()
            scroll.smoothScrollTo(0, 0)
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // A rebuild, or a start while the TV was in touch mode, can leave nothing focused: put the focus back.
        if (hasFocus && currentFocus == null) restoreFocus()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        when (event.keyCode) {
            KeyEvent.KEYCODE_MENU -> {
                if (event.action == KeyEvent.ACTION_UP) showInputs(header.pills.first())
                return true
            }
            KeyEvent.KEYCODE_BACK -> {
                // Home has nowhere to go back to; Back from the grid returns to the dock.
                if (event.action == KeyEvent.ACTION_UP && scroll.scrollY > 0) {
                    (dockCards.firstOrNull() ?: header.pills.first()).requestFocus()
                    scroll.smoothScrollTo(0, 0)
                }
                return true
            }
        }
        return super.dispatchKeyEvent(event)
    }

    // ---------------------------------------------------------------- layout

    private fun buildUi(): View {
        val root = FrameLayout(this)
        root.setBackgroundColor(Color.BLACK)

        wallpaper = SlideshowView(this, SlideshowView.Config(WALLPAPER_MS, kenBurns = false, overlay = false, showClock = false))
        wallpaper.builtinOnly = app.prefs.wallpaper == Prefs.WALLPAPER_BUILTIN
        root.addView(wallpaper, FrameLayout.LayoutParams(MATCH, MATCH))

        // Scrim: darker at the top (clock, pills) and bottom (dock) so text stays readable.
        val scrim = View(this)
        scrim.background = GradientDrawable(
            GradientDrawable.Orientation.TOP_BOTTOM,
            intArrayOf(0xB3000000.toInt(), 0x40000000, 0x26000000, 0x8C000000.toInt(), 0xE6000000.toInt()),
        )
        root.addView(scrim, FrameLayout.LayoutParams(MATCH, MATCH))
        gridShade = View(this)
        gridShade.setBackgroundColor(0xB3000000.toInt())
        gridShade.alpha = 0f
        root.addView(gridShade, FrameLayout.LayoutParams(MATCH, MATCH))
        glow = View(this)
        root.addView(glow, FrameLayout.LayoutParams(MATCH, MATCH))
        setGlow(glowColor)

        scroll = HomeScrollView(this)
        scroll.isVerticalScrollBarEnabled = false
        scroll.overScrollMode = View.OVER_SCROLL_NEVER
        scroll.isFillViewport = true
        root.addView(scroll, FrameLayout.LayoutParams(MATCH, MATCH))

        content = LinearLayout(this)
        content.orientation = LinearLayout.VERTICAL
        content.clipChildren = false
        content.clipToPadding = false
        scroll.addView(content, FrameLayout.LayoutParams(MATCH, WRAP))

        // Hero: exactly one screen tall, header on top, title and dock at the bottom.
        hero = FrameLayout(this)
        hero.clipChildren = false
        content.addView(hero, LinearLayout.LayoutParams(MATCH, resources.displayMetrics.heightPixels))

        header = Header(this, ::showInputs, { launch(Intent(this, PhoneActivity::class.java)) }, ::onPillFocused)
        hero.addView(header.view, frameParams(MATCH, WRAP, Gravity.TOP, dp(48), dp(36), dp(48), 0))

        val bottom = LinearLayout(this)
        bottom.orientation = LinearLayout.VERTICAL
        bottom.clipChildren = false
        bottom.clipToPadding = false
        hero.addView(bottom, frameParams(MATCH, WRAP, Gravity.BOTTOM, dp(48), 0, dp(48), dp(30)))

        title = text(34f, "sans-serif-light", Color.WHITE)
        title.setShadowLayer(dp(18).toFloat(), 0f, 0f, 0x66000000)
        title.setSingleLine(true)
        bottom.addView(title, linearParams(WRAP, WRAP, dp(8), 0, 0, dp(14)))

        dockRow = LinearLayout(this)
        dockRow.orientation = LinearLayout.HORIZONTAL
        dockRow.gravity = Gravity.CENTER
        dockRow.clipChildren = false
        dockRow.clipToPadding = false
        dockRow.setPadding(dp(20), dp(22), dp(20), dp(22))
        dockRow.background = roundRect(0x1FFFFFFF, dp(26).toFloat(), dp(1), 0x14FFFFFF)
        bottom.addView(dockRow, LinearLayout.LayoutParams(MATCH, WRAP))

        appsLabel = text(11f, "sans-serif-medium", 0x99FFFFFF.toInt())
        appsLabel.text = "ALL APPS"
        appsLabel.letterSpacing = 0.22f
        content.addView(appsLabel, linearParams(WRAP, WRAP, dp(68), dp(8), 0, dp(18)))

        grid = LinearLayout(this)
        grid.orientation = LinearLayout.VERTICAL
        grid.clipChildren = false
        grid.clipToPadding = false
        content.addView(grid, linearParams(MATCH, WRAP, dp(68), 0, dp(68), dp(160)))
        return root
    }

    // ---------------------------------------------------------------- apps

    private fun buildApps() {
        // Removing the focused tile makes the framework focus the first view (the Inputs pill), and the focus
        // listeners would then remember that. Keep what was remembered and put the focus back once laid out.
        val keptPkg = lastFocusedPkg
        val keptPill = lastFocusedPill
        rebuilding = true
        try {
            rebuildTiles()
        } finally {
            rebuilding = false
        }
        lastFocusedPkg = keptPkg
        lastFocusedPill = keptPill
        content.post { restoreFocus() }
    }

    private fun rebuildTiles() {
        val repo = app.apps
        repo.markSeen()
        val apps = repo.load(repo.excludedPackages())
        val byPkg = apps.associateBy { it.pkg }
        layout = app.prefs.layout(byPkg.keys)
        val (dockPkgs, gridPkgs) = layout.arrange(apps.map { it.pkg })

        dockRow.removeAllViews()
        grid.removeAllViews()
        dockCards.clear()
        gridCards.clear()

        for ((i, pkg) in dockPkgs.withIndex()) {
            val params = LinearLayout.LayoutParams(0, WRAP, 1f)
            params.setMargins(if (i == 0) 0 else dp(10), 0, if (i == dockPkgs.lastIndex) 0 else dp(10), 0)
            dockRow.addView(tile(byPkg.getValue(pkg), true), params)
        }

        var row: LinearLayout? = null
        for ((i, pkg) in gridPkgs.withIndex()) {
            val col = i % COLUMNS
            if (col == 0) {
                val newRow = LinearLayout(this)
                newRow.clipChildren = false
                newRow.clipToPadding = false
                grid.addView(newRow, linearParams(MATCH, WRAP, 0, 0, 0, dp(26)))
                row = newRow
            }
            val params = LinearLayout.LayoutParams(0, WRAP, 1f)
            params.setMargins(if (col == 0) 0 else dp(10), 0, if (col == COLUMNS - 1) 0 else dp(10), 0)
            row?.addView(tile(byPkg.getValue(pkg), false), params)
        }
        // Pad the last row so its tiles keep their width.
        val lastRow = row
        val remainder = gridPkgs.size % COLUMNS
        if (lastRow != null && remainder != 0) {
            for (col in remainder until COLUMNS) {
                val params = LinearLayout.LayoutParams(0, 1, 1f)
                params.setMargins(dp(10), 0, if (col == COLUMNS - 1) 0 else dp(10), 0)
                lastRow.addView(View(this), params)
            }
        }
        appsLabel.visibility = if (gridPkgs.isEmpty()) View.GONE else View.VISIBLE
        wireFocus()
        dockPkgs.firstOrNull()?.let { byPkg.getValue(it) }?.let { first ->
            title.text = first.label
            setGlow(first.color)
        }
    }

    /** A 16:9 card with the app's banner; grid tiles show the app's name while focused. */
    private fun tile(info: AppInfo, inDock: Boolean): View {
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.clipChildren = false
        box.clipToPadding = false

        val radius = dp(10).toFloat()
        val card = AspectFrame(this)
        card.id = View.generateViewId()
        card.isFocusable = true
        card.isClickable = true
        card.isLongClickable = true
        card.tag = info
        card.clipToOutline = true
        card.outlineProvider = object : ViewOutlineProvider() {
            override fun getOutline(view: View, outline: Outline) =
                outline.setRoundRect(0, 0, view.width, view.height, radius)
        }
        card.background = roundRect(if (info.isBanner) 0xFF1C1C1E.toInt() else darken(info.color, 0.45f), 0f)

        val image = ImageView(this)
        image.setImageDrawable(info.art)
        if (info.isBanner) {
            image.scaleType = ImageView.ScaleType.CENTER_CROP
        } else {
            image.scaleType = ImageView.ScaleType.FIT_CENTER
            image.setPadding(dp(22), dp(14), dp(22), dp(14))
        }
        card.addView(image, FrameLayout.LayoutParams(MATCH, MATCH))

        // Focus ring and sheen, drawn over the art.
        val shine = View(this)
        shine.background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(0x26FFFFFF, 0x00FFFFFF, 0x00FFFFFF)).apply {
            cornerRadius = radius
            setStroke(dp(2), 0xE6FFFFFF.toInt())
        }
        shine.alpha = 0f
        card.addView(shine, FrameLayout.LayoutParams(MATCH, MATCH))
        box.addView(card, LinearLayout.LayoutParams(MATCH, WRAP))

        val label = text(11f, "sans-serif-medium", Color.WHITE)
        label.text = info.label
        label.setSingleLine(true)
        label.gravity = Gravity.CENTER
        label.alpha = 0f
        if (!inDock) box.addView(label, linearParams(MATCH, WRAP, 0, dp(10), 0, 0))

        card.setOnClickListener {
            launch(
                Intent(Intent.ACTION_MAIN)
                    .addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER)
                    .setComponent(info.component)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED),
            )
        }
        card.setOnLongClickListener {
            showTileMenu(card, info, inDock)
            true
        }
        card.setOnFocusChangeListener { v, has ->
            val scale = if (!has) 1f else if (inDock) 1.12f else 1.1f
            v.animate().scaleX(scale).scaleY(scale).translationZ(if (has) dp(14).toFloat() else 0f)
                .setInterpolator(if (has) OvershootInterpolator(1.8f) else DecelerateInterpolator())
                .setDuration(if (has) 320L else 200L)
                .start()
            shine.animate().alpha(if (has) 1f else 0f).setDuration(160).start()
            label.animate().alpha(if (has) 1f else 0f).translationY(if (has) dp(4).toFloat() else 0f).setDuration(200).start()
            if (has) onTileFocused(info, inDock, box)
        }
        (if (inDock) dockCards else gridCards).add(card)
        return box
    }

    private fun onTileFocused(info: AppInfo, inDock: Boolean, box: View) {
        if (rebuilding) return
        lastFocusedPkg = info.pkg
        lastFocusedPill = View.NO_ID
        if (inDock) {
            title.text = info.label
            title.animate().alpha(1f).setStartDelay(0).setDuration(200).start()
            animateGlow(info.color)
            fadeGlow(1f)
            scroll.smoothScrollTo(0, 0)
        } else {
            fadeGlow(0.35f)
            title.animate().alpha(0f).setStartDelay(0).setDuration(200).start()
            scrollToRow(box)
        }
    }

    private fun onPillFocused(pill: View) {
        if (rebuilding) return
        lastFocusedPill = pill.id
        lastFocusedPkg = null
        scroll.smoothScrollTo(0, 0)
        fadeGlow(1f)
    }

    private fun restoreFocus() {
        val tile = (dockCards + gridCards).firstOrNull { (it.tag as AppInfo).pkg == lastFocusedPkg }
        val pill = header.pills.firstOrNull { it.id == lastFocusedPill }
        (tile ?: pill ?: dockCards.firstOrNull() ?: gridCards.firstOrNull() ?: header.pills.first()).requestFocus()
    }

    /** Explicit D-pad targets: one row per press, no wrapping at the ends of rows or the pill bar. */
    private fun wireFocus() {
        val pills = header.pills
        val d = dockCards.size
        val g = gridCards.size
        val belowPills = dockCards.getOrNull(minOf(d - 1, d / 2)) ?: gridCards.firstOrNull()
        for ((i, p) in pills.withIndex()) {
            p.nextFocusLeftId = (if (i == 0) p else pills[i - 1]).id
            p.nextFocusRightId = (if (i == pills.lastIndex) p else pills[i + 1]).id
            p.nextFocusUpId = p.id
            p.nextFocusDownId = (belowPills ?: p).id
        }
        val topId = pills.first().id
        for ((i, c) in dockCards.withIndex()) {
            c.nextFocusUpId = topId
            c.nextFocusLeftId = (if (i == 0) c else dockCards[i - 1]).id
            c.nextFocusRightId = (if (i == d - 1) c else dockCards[i + 1]).id
            c.nextFocusDownId = (if (g > 0) gridCards[minOf(i, COLUMNS - 1, g - 1)] else c).id
        }
        val lastRow = (g - 1) / COLUMNS
        for ((j, c) in gridCards.withIndex()) {
            val r = j / COLUMNS
            val col = j % COLUMNS
            c.nextFocusUpId = when {
                r > 0 -> gridCards[j - COLUMNS].id
                d > 0 -> dockCards[minOf(col, d - 1)].id
                else -> topId
            }
            c.nextFocusDownId = when {
                j + COLUMNS < g -> gridCards[j + COLUMNS].id
                r < lastRow -> gridCards[g - 1].id
                else -> c.id
            }
            c.nextFocusLeftId = (if (col == 0) c else gridCards[j - 1]).id
            c.nextFocusRightId = (if (col == COLUMNS - 1 || j == g - 1) c else gridCards[j + 1]).id
        }
    }

    private fun scrollToRow(tileBox: View) {
        val row = tileBox.parent as View
        val screenHeight = resources.displayMetrics.heightPixels
        // First grid row: slide the dock band up to the top edge. Later rows sit a little above centre.
        val band = dockRow.parent as View
        val target = if (grid.indexOfChild(row) == 0) {
            hero.top + band.top + dockRow.top - dp(22)
        } else {
            grid.top + row.top - (screenHeight * 0.42f).toInt()
        }
        val max = maxOf(0, content.height - scroll.height)
        scroll.smoothScrollTo(0, target.coerceIn(0, max))
    }

    // ---------------------------------------------------------------- menus

    private fun showInputs(anchor: View) {
        popup?.dismiss()
        popup = PopupList.show(anchor, Inputs.items(this, app.apps), dp(220)) {
            popup = null
            anchor.requestFocus()
        }
    }

    private fun showTileMenu(anchor: View, info: AppInfo, inDock: Boolean) {
        val items = ArrayList<PopupList.Item>()
        if (inDock) {
            val i = layout.dock.indexOf(info.pkg)
            if (i > 0) items += PopupList.Item("Move left") { app.prefs.saveLayout(layout.moveLeft(info.pkg)) }
            if (i in 0 until layout.dock.lastIndex) items += PopupList.Item("Move right") { app.prefs.saveLayout(layout.moveRight(info.pkg)) }
            items += PopupList.Item("Remove from dock") { app.prefs.saveLayout(layout.removeFromDock(info.pkg)) }
        } else if (layout.dock.size < HomeLayout.MAX_DOCK) {
            items += PopupList.Item("Add to dock") { app.prefs.saveLayout(layout.addToDock(info.pkg)) }
        }
        items += PopupList.Item("Hide") {
            lastFocusedPkg = null
            app.prefs.saveLayout(layout.hide(info.pkg))
        }
        items += PopupList.Item("App info") {
            launch(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + info.pkg)))
        }
        popup?.dismiss()
        popup = PopupList.show(anchor, items, dp(200)) {
            popup = null
            restoreFocus()
        }
    }

    // ---------------------------------------------------------------- glow

    private fun setGlow(color: Int) {
        glowColor = color
        val g = GradientDrawable()
        g.setGradientType(GradientDrawable.RADIAL_GRADIENT)
        g.setGradientCenter(0.5f, 0.78f)
        g.setGradientRadius(resources.displayMetrics.widthPixels * 0.62f)
        g.setColors(intArrayOf(withAlpha(color, 0.5f), withAlpha(color, 0.14f), Color.TRANSPARENT))
        glow.background = g
    }

    private fun animateGlow(to: Int) {
        glowAnim?.cancel()
        val animator = ValueAnimator.ofObject(ArgbEvaluator(), glowColor, to)
        animator.duration = 450
        animator.addUpdateListener { setGlow(it.animatedValue as Int) }
        animator.start()
        glowAnim = animator
    }

    private fun fadeGlow(alpha: Float) {
        glow.animate().alpha(alpha).setDuration(300).start()
        // The grid sits over the photo's busiest part, so dim the photo while browsing apps.
        gridShade.animate().alpha(if (alpha < 1f) 1f else 0f).setDuration(300).start()
    }

    // ---------------------------------------------------------------- entrance

    private fun playEntrance() {
        val top = header.view
        top.alpha = 0f
        top.translationY = -dp(12).toFloat()
        top.animate().alpha(1f).translationY(0f).setDuration(500).setStartDelay(80).start()
        title.alpha = 0f
        title.animate().alpha(1f).setDuration(500).setStartDelay(200).start()
        for (i in 0 until dockRow.childCount) {
            val t = dockRow.getChildAt(i)
            t.alpha = 0f
            t.translationY = dp(24).toFloat()
            t.animate().alpha(1f).translationY(0f).setInterpolator(DecelerateInterpolator(2f))
                .setDuration(450).setStartDelay(160 + i * 60L).start()
        }
    }

    private companion object {
        const val COLUMNS = 5
        const val WALLPAPER_MS = 10 * 60_000L
    }
}
