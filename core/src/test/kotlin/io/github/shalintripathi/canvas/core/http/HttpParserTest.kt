package io.github.shalintripathi.canvas.core.http

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.EOFException

class HttpParserTest {
    private fun parse(raw: String, maxBody: Long = 1024) =
        HttpParser.parse(ByteArrayInputStream(raw.toByteArray(Charsets.ISO_8859_1)), maxBody)

    private fun status(raw: String, maxBody: Long = 1024): Int = try {
        parse(raw, maxBody)
        fail("expected HttpException")
        0
    } catch (e: HttpException) {
        e.status
    }

    @Test
    fun parsesMethodPathQueryHeadersAndCookies() {
        val r = parse("GET /pair?t=ab%2Fc&x=1+2 HTTP/1.1\r\nHost: tv\r\nCookie: a=1; canvas_session=xyz\r\n\r\n")
        assertEquals("GET", r.method)
        assertEquals("/pair", r.path)
        assertEquals("ab/c", r.query["t"])
        assertEquals("1 2", r.query["x"])
        assertEquals("tv", r.header("HOST"))
        assertEquals("xyz", r.cookies["canvas_session"])
        assertEquals(0L, r.contentLength)
    }

    @Test
    fun decodesPercentEscapesInPathButKeepsPlus() {
        assertEquals("/api/apps/a+b c/icon", parse("GET /api/apps/a+b%20c/icon HTTP/1.1\r\n\r\n").path)
    }

    @Test
    fun readsExactlyContentLengthBytesOfBody() {
        val r = parse("PUT /api/photos HTTP/1.1\r\nContent-Length: 5\r\n\r\nhelloEXTRA")
        assertEquals("hello", r.bodyText())
    }

    @Test
    fun rejectsOversizedBodies() {
        assertEquals(413, status("PUT /x HTTP/1.1\r\nContent-Length: 2048\r\n\r\n", maxBody = 1024))
    }

    @Test
    fun rejectsOversizedHeads() {
        assertEquals(431, status("GET /x HTTP/1.1\r\nX: " + "a".repeat(HttpParser.MAX_HEAD) + "\r\n\r\n"))
    }

    @Test
    fun rejectsMalformedRequests() {
        assertEquals(400, status("NONSENSE\r\n\r\n"))
        assertEquals(400, status("GET x HTTP/1.1\r\n\r\n"))
        assertEquals(400, status("GET /x HTTP/1.1\r\nNoColonHere\r\n\r\n"))
        assertEquals(400, status("PUT /x HTTP/1.1\r\nContent-Length: abc\r\n\r\n"))
        assertEquals(501, status("PUT /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"))
    }

    @Test(expected = EOFException::class)
    fun emptyConnectionIsEof() {
        parse("")
    }

    @Test
    fun missingCookieHeaderMeansNoCookies() {
        assertNull(parse("GET / HTTP/1.1\r\n\r\n").cookies["canvas_session"])
    }

    @Test
    fun bodyEndingEarlyThrowsOnSingleByteReads() {
        val body = parse("PUT /x HTTP/1.1\r\nContent-Length: 5\r\n\r\nhi").body
        assertEquals('h'.code, body.read())
        assertEquals('i'.code, body.read())
        try {
            body.read()
            fail("expected EOFException")
        } catch (e: EOFException) {
            assertEquals("Body ended early", e.message)
        }
    }

    @Test(expected = EOFException::class)
    fun bodyEndingEarlyThrowsOnBulkReads() {
        parse("PUT /x HTTP/1.1\r\nContent-Length: 5\r\n\r\nhi").body.readBytes()
    }
}
