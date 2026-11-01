package io.github.shalintripathi.canvas.util

import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/** Writes via a temp file, synced to disk, and a rename, so a crash or power cut never leaves half a file. */
fun File.writeAtomically(text: String) {
    val tmp = File(parentFile, "$name.tmp")
    FileOutputStream(tmp).use {
        it.write(text.toByteArray())
        it.fd.sync()
    }
    if (!tmp.renameTo(this)) {
        delete()
        if (!tmp.renameTo(this)) throw IOException("Could not write $name")
    }
}
