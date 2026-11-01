package io.github.shalintripathi.canvas.core.http

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ExecutorService
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit

/**
 * One request per connection (Connection: close). Requests run on a small fixed pool behind a short
 * bounded queue; a connection that arrives when both are full is closed without a response.
 * [headTimeoutMs] bounds each read while the request line and headers arrive.
 */
class HttpServer(
    private val port: Int,
    private val maxBody: Long,
    private val handler: HttpHandler,
    private val threads: Int = 8,
    private val headTimeoutMs: Int = 10_000,
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
        val workers = ThreadPoolExecutor(threads, threads, 0L, TimeUnit.MILLISECONDS, ArrayBlockingQueue<Runnable>(QUEUE_SIZE))
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
                null
            }
            if (client == null) {
                // Closed means stop(). Any other failure (for example out of file descriptors) is transient.
                if (socket.isClosed) return
                try {
                    Thread.sleep(ACCEPT_BACKOFF_MS)
                } catch (e: InterruptedException) {
                    return
                }
                continue
            }
            try {
                workers.execute { serve(client) }
            } catch (e: RejectedExecutionException) {
                closeQuietly(client)
            }
        }
    }

    private fun serve(client: Socket) {
        client.use {
            it.soTimeout = headTimeoutMs
            val input = BufferedInputStream(it.getInputStream())
            val output = BufferedOutputStream(it.getOutputStream())
            val request = try {
                HttpParser.parse(input, maxBody)
            } catch (e: IOException) {
                return // EOF, timeout or reset before a full head: nothing to answer
            } catch (e: HttpException) {
                respond(it, output, HttpResponse.error(e.status, e.message ?: "Error"))
                // The body was never parsed. Drain what the client is still sending so the response is not reset.
                if (e.bodyLength >= 0) {
                    drain(it, input, minOf(e.bodyLength, MAX_DRAIN), 2_000)
                } else {
                    drain(it, input, SMALL_DRAIN, 1_000)
                }
                return
            }
            it.soTimeout = BODY_TIMEOUT_MS
            val response = try {
                handler.handle(request)
            } catch (e: EOFException) {
                return // the client stopped sending the body
            } catch (e: SocketTimeoutException) {
                return
            } catch (e: HttpException) {
                HttpResponse.error(e.status, e.message ?: "Error")
            } catch (e: Exception) {
                HttpResponse.error(500, "Internal error")
            }
            respond(it, output, response)
            drain(it, request.body, MAX_DRAIN, 2_000)
        }
    }

    /** Writes the response and half-closes, so the client sees the end of the response before we drain. */
    private fun respond(socket: Socket, output: OutputStream, response: HttpResponse) {
        try {
            write(output, response)
            socket.shutdownOutput()
        } catch (e: IOException) {
            // The phone went away mid-response.
        }
    }

    /**
     * Reads and discards up to [cap] bytes the client is still sending, for at most [budgetMs].
     * Closing a socket with unread data makes the kernel send RST, which can destroy the response.
     */
    private fun drain(socket: Socket, source: InputStream, cap: Long, budgetMs: Long) {
        val deadline = System.nanoTime() + budgetMs * 1_000_000
        val buf = ByteArray(8192)
        var left = cap
        try {
            while (left > 0) {
                val waitMs = (deadline - System.nanoTime()) / 1_000_000
                if (waitMs <= 0) return
                socket.soTimeout = waitMs.toInt()
                val n = source.read(buf, 0, minOf(buf.size.toLong(), left).toInt())
                if (n < 0) return
                left -= n
            }
        } catch (e: IOException) {
            // Timed out or the client went away; closing is all that is left.
        }
    }

    private fun closeQuietly(socket: Socket) {
        try {
            socket.close()
        } catch (e: IOException) {
            // Nothing useful to do with a socket that will not close.
        }
    }

    companion object {
        private const val QUEUE_SIZE = 16
        private const val ACCEPT_BACKOFF_MS = 100L
        private const val BODY_TIMEOUT_MS = 30_000
        private const val MAX_DRAIN = 1024L * 1024
        private const val SMALL_DRAIN = 64L * 1024

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
