package io.github.shalintripathi.canvas.core.http

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException

/** One request per connection (Connection: close), handled on a small thread pool. */
class HttpServer(
    private val port: Int,
    private val maxBody: Long,
    private val handler: HttpHandler,
    private val threads: Int = 4,
) {
    private var server: ServerSocket? = null
    private var pool: ExecutorService? = null

    val localPort: Int get() = server?.localPort ?: -1

    @Synchronized
    fun start() {
        if (server != null) return
        val socket = ServerSocket()
        socket.reuseAddress = true
        socket.bind(InetSocketAddress(port))
        val workers = Executors.newFixedThreadPool(threads)
        server = socket
        pool = workers
        Thread({ acceptLoop(socket, workers) }, "canvas-http").apply { isDaemon = true }.start()
    }

    @Synchronized
    fun stop() {
        server?.close()
        server = null
        pool?.shutdownNow()
        pool = null
    }

    private fun acceptLoop(socket: ServerSocket, workers: ExecutorService) {
        while (!socket.isClosed) {
            val client = try {
                socket.accept()
            } catch (e: IOException) {
                return
            }
            try {
                workers.execute { serve(client) }
            } catch (e: RejectedExecutionException) {
                client.close()
            }
        }
    }

    private fun serve(client: Socket) {
        client.use {
            it.soTimeout = 30_000
            val input = BufferedInputStream(it.getInputStream())
            val output = BufferedOutputStream(it.getOutputStream())
            val response = try {
                handler.handle(HttpParser.parse(input, maxBody))
            } catch (e: EOFException) {
                return
            } catch (e: SocketTimeoutException) {
                return
            } catch (e: HttpException) {
                HttpResponse.error(e.status, e.message ?: "Error")
            } catch (e: Exception) {
                HttpResponse.error(500, "Internal error")
            }
            try {
                write(output, response)
            } catch (e: IOException) {
                // The phone went away mid-response.
            }
        }
    }

    companion object {
        fun write(out: OutputStream, r: HttpResponse) {
            val length = r.file?.length() ?: r.body.size.toLong()
            val head = StringBuilder()
                .append("HTTP/1.1 ").append(r.status).append(' ').append(reason(r.status)).append("\r\n")
                .append("Content-Type: ").append(r.contentType).append("\r\n")
                .append("Content-Length: ").append(length).append("\r\n")
                .append("Connection: close\r\n")
                .append("X-Content-Type-Options: nosniff\r\n")
            if (r.headers.keys.none { it.equals("Cache-Control", ignoreCase = true) }) head.append("Cache-Control: no-store\r\n")
            for ((name, value) in r.headers) head.append(name).append(": ").append(value).append("\r\n")
            head.append("\r\n")
            out.write(head.toString().toByteArray(Charsets.ISO_8859_1))
            val file = r.file
            if (file != null) file.inputStream().use { it.copyTo(out) } else out.write(r.body)
            out.flush()
        }

        private fun reason(status: Int) = when (status) {
            200 -> "OK"
            201 -> "Created"
            302 -> "Found"
            400 -> "Bad Request"
            401 -> "Unauthorized"
            403 -> "Forbidden"
            404 -> "Not Found"
            409 -> "Conflict"
            410 -> "Gone"
            413 -> "Payload Too Large"
            415 -> "Unsupported Media Type"
            431 -> "Request Header Fields Too Large"
            501 -> "Not Implemented"
            else -> if (status >= 500) "Server Error" else "Status"
        }
    }
}
