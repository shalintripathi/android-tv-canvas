package io.github.shalintripathi.canvas.core.http

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.net.HttpURLConnection
import java.net.URL

class HttpServerTest {
    private lateinit var server: HttpServer

    @Before
    fun start() {
        server = HttpServer(0, 1024, HttpHandler { r ->
            when {
                r.path == "/hello" -> HttpResponse.json("{\"name\":${HttpResponse.quote(r.query["name"].orEmpty())}}")
                r.method == "PUT" -> HttpResponse.json("{\"length\":${r.bodyText().length}}", 201)
                r.path == "/boom" -> throw IllegalStateException("boom")
                else -> HttpResponse.error(404, "Not found")
            }
        })
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
}
