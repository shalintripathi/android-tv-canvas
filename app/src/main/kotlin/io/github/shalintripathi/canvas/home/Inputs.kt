package io.github.shalintripathi.canvas.home

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.tv.TvContract
import android.media.tv.TvInputInfo
import android.media.tv.TvInputManager
import android.provider.Settings
import io.github.shalintripathi.canvas.apps.AppRepository
import io.github.shalintripathi.canvas.ui.launch

/** Entries for the Inputs menu: HDMI and AV inputs, then the TV's Live TV app. */
object Inputs {
    fun items(activity: Activity, repo: AppRepository): List<PopupList.Item> {
        val items = ArrayList<PopupList.Item>()
        val liveTv = repo.liveTvPackage()
        val openLiveTv = { liveTv?.let { repo.launchIntent(it) }?.let { activity.launch(it) } }
        val inputs = try {
            val manager = activity.getSystemService(Context.TV_INPUT_SERVICE) as? TvInputManager
            manager?.tvInputList.orEmpty().filter { it.isPassthroughInput && !it.isHidden(activity) }
        } catch (e: RuntimeException) {
            emptyList()
        }
        for (info in inputs.sortedBy { label(activity, it).lowercase() }) {
            items += PopupList.Item(label(activity, info)) {
                val intent = Intent(Intent.ACTION_VIEW, TvContract.buildChannelUriForPassthroughInput(info.id))
                if (!activity.launch(intent)) openLiveTv()
            }
        }
        if (liveTv != null) items += PopupList.Item("Live TV") { openLiveTv() }
        if (items.isEmpty()) items += PopupList.Item("Settings") { activity.launch(Intent(Settings.ACTION_SETTINGS)) }
        return items
    }

    private fun label(context: Context, info: TvInputInfo): String {
        val custom = info.loadCustomLabel(context)
        return (if (custom.isNullOrEmpty()) info.loadLabel(context) else custom).toString()
    }
}
