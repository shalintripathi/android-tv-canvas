package io.github.shalintripathi.canvas.core.http

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.HttpURLConnection
import java.net.Socket
import java.net.URL

class HttpServerTest {
    private lateinit var server: HttpServer

    private fun routes() = HttpHandler { r ->
        when {
            r.path == "/hello" -> HttpResponse.json("{\"name\":${HttpResponse.quote(r.query["name"].orEmpty())}}")
            r.method == "PUT" -> HttpResponse.json("{\"length\":${r.bodyText().length}}", 201)
            r.path == "/boom" -> throw IllegalStateException("boom")
            else -> HttpResponse.error(404, "Not found")
        }
    }

    @Before
    fun start() {
        server = HttpServer(0, 1024, routes())
        server.start()
    }

    @After
    fun stop() = server.stop()

    private fun open(path: String) =
        URL("http://127.0.0.1:${server.localPort}$path").openConnection() as HttpURLConnection

    @Test
    fun servesJson() {
        val c = open("/hello?name=Tara%20%22T%22")
        assertEquals(200, c.responseCode)
        assertEquals("application/json; charset=utf-8", c.contentType)
        assertEquals("{\"name\":\"Tara \\\"T\\\"\"}", c.inputStream.readBytes().toString(Charsets.UTF_8))
    }

    @Test
    fun acceptsBodies() {
        val c = open("/upload")
        c.requestMethod = "PUT"
        c.doOutput = true
        c.outputStream.use { it.write("abcdef".toByteArray()) }
        assertEquals(201, c.responseCode)
        assertEquals("{\"length\":6}", c.inputStream.readBytes().toString(Charsets.UTF_8))
    }

    @Test
    fun handlerErrorsBecome500() {
        assertEquals(500, open("/boom").responseCode)
    }

    @Test
    fun unknownPathsAre404() {
        assertEquals(404, open("/nope").responseCode)
    }

    @Test
    fun oversizedBodyGetsA413InsteadOfAReset() {
        Socket("127.0.0.1", server.localPort).use { s ->
            s.soTimeout = 5_000
            s.getOutputStream().apply {
                write("PUT /upload HTTP/1.1\r\nHost: tv\r\nContent-Length: 2048\r\n\r\n".toByteArray(Charsets.ISO_8859_1))
                write(ByteArray(2048) { 'a'.code.toByte() })
                flush()
            }
            val reply = s.getInputStream().readBytes().toString(Charsets.ISO_8859_1)
            assertTrue(reply, reply.startsWith("HTTP/1.1 413"))
        }
    }

    @Test
    fun idleConnectionsDoNotStarveTheServer() {
        val slow = HttpServer(0, 1024, routes(), threads = 2, headTimeoutMs = 300)
        slow.start()
        val idle = List(2) { Socket("127.0.0.1", slow.localPort) }
        try {
            val c = URL("http://127.0.0.1:${slow.localPort}/hello?name=x").openConnection() as HttpURLConnection
            c.connectTimeout = 2_000
            c.readTimeout = 2_000
            assertEquals(200, c.responseCode)
        } finally {
            idle.forEach { it.close() }
            slow.stop()
        }
    }

    @Test
    fun canStopThenStartAgainOnANewServer() {
        server.stop()
        val again = HttpServer(0, 1024, routes())
        again.start()
        try {
            val c = URL("http://127.0.0.1:${again.localPort}/hello?name=x").openConnection() as HttpURLConnection
            assertEquals(200, c.responseCode)
        } finally {
            again.stop()
        }
    }
}
