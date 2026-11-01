package io.github.shalintripathi.canvas.util

import java.io.File
import java.io.IOException

/** Writes via a temp file and rename so a crash never leaves half a file. */
fun File.writeAtomically(text: String) {
    val tmp = File(parentFile, "$name.tmp")
    tmp.writeText(text)
    if (!tmp.renameTo(this)) {
        delete()
        if (!tmp.renameTo(this)) throw IOException("Could not write $name")
    }
}
