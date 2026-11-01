package io.github.shalintripathi.canvas.phone

import io.github.shalintripathi.canvas.core.Phone
import io.github.shalintripathi.canvas.core.PhoneStore
import io.github.shalintripathi.canvas.core.PhonesJson
import io.github.shalintripathi.canvas.util.writeAtomically
import java.io.File

class FilePhoneStore(private val file: File) : PhoneStore {
    override fun load(): List<Phone> = if (file.exists()) PhonesJson.decode(file.readText()) else emptyList()

    override fun save(phones: List<Phone>) = file.writeAtomically(PhonesJson.encode(phones))
}
