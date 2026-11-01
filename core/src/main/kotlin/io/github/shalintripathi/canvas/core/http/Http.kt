package io.github.shalintripathi.canvas.core.http

import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.File
import java.io.InputStream
import java.net.URLDecoder

class HttpException(val status: Int, message: String) : Exception(message)

class HttpRequest(
    val method: String,
    val path: String,
    val query: Map<String, String>,
    val headers: Map<String, String>,
    val contentLength: Long,
    val body: InputStream,
) {
    fun header(name: String): String? = headers[name.lowercase()]

    val cookies: Map<String, String> by lazy {
        header("cookie").orEmpty().split(';').mapNotNull { part ->
            val eq = part.indexOf('=')
            if (eq <= 0) null else part.substring(0, eq).trim() to part.substring(eq + 1).trim()
        }.toMap()
    }

    /** A small body (JSON forms) as UTF-8. */
    fun bodyText(limit: Int = 64 * 1024): String {
        if (contentLength > limit) throw HttpException(413, "Body too large")
        return body.readBytes().toString(Charsets.UTF_8)
    }
}

class HttpResponse(
    val status: Int,
    val contentType: String,
    val body: ByteArray = ByteArray(0),
    val file: File? = null,
    val headers: Map<String, String> = emptyMap(),
) {
    companion object {
        private const val JSON = "application/json; charset=utf-8"

        fun json(json: String, status: Int = 200, headers: Map<String, String> = emptyMap()) =
            HttpResponse(status, JSON, json.toByteArray(Charsets.UTF_8), headers = headers)

        fun error(status: Int, message: String) = json("{\"error\":${quote(message)}}", status)

        fun bytes(bytes: ByteArray, type: String, headers: Map<String, String> = emptyMap()) =
            HttpResponse(200, type, bytes, headers = headers)

        fun file(file: File, type: String, headers: Map<String, String> = emptyMap()) =
            HttpResponse(200, type, file = file, headers = headers)

        fun redirect(location: String, headers: Map<String, String> = emptyMap()) =
            HttpResponse(302, "text/plain; charset=utf-8", headers = headers + ("Location" to location))

        /** A JSON string literal. */
        fun quote(s: String): String {
            val out = StringBuilder(s.length + 2).append('"')
            for (c in s) when {
                c == '"' -> out.append("\\\"")
                c == '\\' -> out.append("\\\\")
                c == '\n' -> out.append("\\n")
                c == '\r' -> out.append("\\r")
                c == '\t' -> out.append("\\t")
                c < ' ' -> out.append(String.format("\\u%04x", c.code))
                else -> out.append(c)
            }
            return out.append('"').toString()
        }
    }
}

fun interface HttpHandler {
    fun handle(request: HttpRequest): HttpResponse
}

/** Body stream that stops after Content-Length bytes and never closes the socket. */
class LimitedInputStream(private val input: InputStream, private var remaining: Long) : InputStream() {
    override fun read(): Int {
        if (remaining <= 0) return -1
        val b = input.read()
        if (b < 0) remaining = 0 else remaining--
        return b
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (remaining <= 0) return -1
        val n = input.read(b, off, minOf(len.toLong(), remaining).toInt())
        if (n < 0) remaining = 0 else remaining -= n
        return n
    }

    override fun close() = Unit
}

object HttpParser {
    const val MAX_HEAD = 16 * 1024
    private const val CR = '\r'.code
    private const val LF = '\n'.code

    fun parse(input: InputStream, maxBody: Long): HttpRequest {
        val lines = readHead(input).split("\r\n")
        val parts = lines.first().split(' ')
        if (parts.size != 3 || !parts[2].startsWith("HTTP/1.")) throw HttpException(400, "Bad request line")
        val target = parts[1]
        if (!target.startsWith("/")) throw HttpException(400, "Bad request target")
        val q = target.indexOf('?')
        val path = decode(if (q < 0) target else target.substring(0, q), keepPlus = true)
        val query = if (q < 0) emptyMap() else parseQuery(target.substring(q + 1))

        val headers = HashMap<String, String>()
        for (line in lines.drop(1)) {
            if (line.isEmpty()) continue
            val colon = line.indexOf(':')
            if (colon <= 0) throw HttpException(400, "Bad header")
            headers[line.substring(0, colon).trim().lowercase()] = line.substring(colon + 1).trim()
        }
        if (headers.containsKey("transfer-encoding")) throw HttpException(501, "Chunked bodies are not supported")
        val length = headers["content-length"]?.let { it.toLongOrNull() ?: throw HttpException(400, "Bad Content-Length") } ?: 0L
        if (length < 0) throw HttpException(400, "Bad Content-Length")
        if (length > maxBody) throw HttpException(413, "Body too large")
        return HttpRequest(parts[0].uppercase(), path, query, headers, length, LimitedInputStream(input, length))
    }

    private fun readHead(input: InputStream): String {
        val buf = ByteArrayOutputStream()
        var matched = 0 // how much of "\r\n\r\n" has been seen
        while (matched < 4) {
            val b = input.read()
            if (b < 0) {
                if (buf.size() == 0) throw EOFException()
                throw HttpException(400, "Incomplete request")
            }
            buf.write(b)
            if (buf.size() > MAX_HEAD) throw HttpException(431, "Headers too large")
            matched = when (matched) {
                0, 2 -> if (b == CR) matched + 1 else 0
                else -> if (b == LF) matched + 1 else if (b == CR) 1 else 0
            }
        }
        val bytes = buf.toByteArray()
        return String(bytes, 0, bytes.size - 4, Charsets.ISO_8859_1)
    }

    private fun parseQuery(s: String): Map<String, String> = s.split('&').filter { it.isNotEmpty() }.associate { pair ->
        val eq = pair.indexOf('=')
        if (eq < 0) decode(pair) to "" else decode(pair.substring(0, eq)) to decode(pair.substring(eq + 1))
    }

    private fun decode(s: String, keepPlus: Boolean = false): String = try {
        URLDecoder.decode(if (keepPlus) s.replace("+", "%2B") else s, "UTF-8")
    } catch (e: IllegalArgumentException) {
        throw HttpException(400, "Bad percent-encoding")
    }
}
