package com.assetstudio.library.catalog

import com.assetstudio.library.download.AssetDownloader
import com.assetstudio.library.download.DownloadState
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/**
 * Fetching the static GitHub Pages catalog over a real HTTP stack.
 *
 * MockWebServer stands in for the Pages host: the same requests the app makes
 * (`GET <base>/catalog/index.json`, `GET <base>/assets/…`) are served by a socket,
 * so URL building, JSON decoding, ETag revalidation, the offline fallback and the
 * streaming download with digest verification are all exercised for real. Nothing
 * here talks to a dynamic backend — the server returns static files only.
 */
class CatalogClientTest {

    @get:Rule val temp = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var cacheDir: File
    private lateinit var client: CatalogClient

    private val indexJson = """
        {
          "schema_version": "1.0.0",
          "kind": "asset-studio-static-catalog",
          "generated_at": "2026-01-01T00:00:00+00:00",
          "base_url": "BASE",
          "counts": {"models": 1, "animations": 1},
          "models": [{
            "id": "model_a", "kind": "model", "name": "Scout", "category": "character",
            "source_format": "GLB", "format": "GLB", "bytes": 12,
            "sha256": "SHA_A", "validation_status": "PASS", "humanoid": true,
            "clip_count": 1, "updated_at": "2026-01-01T00:00:00+00:00",
            "download_url": "BASE/assets/models/model_a/model_a.glb",
            "manifest_url": "BASE/catalog/models/model_a.json",
            "gltf_url": "BASE/assets/models/model_a/scene.gltf",
            "dependencies": [
              {"role": "buffer", "path": "assets/models/model_a/scene.bin",
               "url": "BASE/assets/models/model_a/scene.bin", "bytes": 4, "sha256": "BIN_SHA"},
              {"role": "texture", "path": "assets/models/model_a/textures/0.png",
               "url": "BASE/assets/models/model_a/textures/0.png", "bytes": 3, "sha256": "PNG_SHA"}
            ]
          }],
          "animations": []
        }
    """.trimIndent()

    private fun sha(bytes: ByteArray) =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
        cacheDir = temp.newFolder("cache")
        client = CatalogClient(OkHttpClient(), cacheDir)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun base(): String = server.url("/").toString().trimEnd('/')

    private fun indexBody(): String = indexJson.replace("BASE", base())

    @Test
    fun `index is fetched from the pages host and decoded`() = runTest {
        server.enqueue(MockResponse().setBody(indexBody()).addHeader("ETag", "\"v1\""))
        val (index, fromCache) = client.index(base())

        assertFalse(fromCache)
        assertEquals("asset-studio-static-catalog", index.kind)
        assertEquals(1, index.models.size)
        assertEquals("model_a", index.models[0].id)
        assertEquals("${base()}/catalog/index.json",
            server.takeRequest().path?.let { base() + it })
        assertTrue(index.models[0].hasGltfBundle)
        assertEquals(2, index.models[0].dependencies.size)
    }

    @Test
    fun `the cached catalog is reused when the pages host is unreachable`() = runTest {
        server.enqueue(MockResponse().setBody(indexBody()))
        client.index(base())

        server.shutdown() // no network from here on
        val (index, fromCache) = client.index(base(), forceRefresh = true)

        assertTrue("offline refresh must fall back to the cached copy", fromCache)
        assertEquals("model_a", index.models[0].id)
    }

    @Test
    fun `revalidation asks with If-None-Match and accepts 304`() = runTest {
        server.enqueue(MockResponse().setBody(indexBody()).addHeader("ETag", "\"v1\""))
        client.index(base())
        server.takeRequest()

        server.enqueue(MockResponse().setResponseCode(304))
        val (index, fromCache) = client.index(base(), forceRefresh = false)

        val request = server.takeRequest()
        assertEquals("\"v1\"", request.getHeader("If-None-Match"))
        assertTrue(fromCache)
        assertEquals(1, index.models.size)
    }

    @Test
    fun `an empty catalog is reported instead of pretending there are assets`() = runTest {
        server.enqueue(MockResponse().setBody("""{"kind":"asset-studio-static-catalog"}"""))
        try {
            client.index(base())
            fail("an empty catalog must not look like a successful refresh")
        } catch (e: IOException) {
            assertTrue(e.message!!.contains("empty"))
        }
    }

    @Test
    fun `an html 404 page is not mistaken for a catalog`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404).setBody("<html>not found</html>"))
        try {
            client.index(base())
            fail("Pages 404 must surface as an error naming the URL")
        } catch (e: IOException) {
            assertTrue(e.message!!.contains("404"))
            assertTrue(e.message!!.contains("/catalog/index.json"))
        }
    }

    @Test
    fun `downloads verify the published digest and land at the catalog path`() = runTest {
        val payload = "glTF-ish bytes".toByteArray()
        val expected = sha(payload)
        server.enqueue(MockResponse().setBody(okio.Buffer().write(payload)))

        val downloader = AssetDownloader(OkHttpClient(), temp.newFolder("assets"))
        val states = downloader.download(base() + "/assets/models/model_a/model_a.glb",
            "model_a/model_a.glb", expected, payload.size.toLong()).toList()

        val finished = states.filterIsInstance<DownloadState.Finished>().single()
        assertEquals(expected, finished.sha256)
        assertEquals(payload.size.toLong(), finished.bytes)
        val expectedPath = File(downloader.root, "model_a/model_a.glb")
        assertEquals(expectedPath.absolutePath, finished.file.absolutePath)
        assertTrue("downloaded file must exist on disk", expectedPath.isFile)
        assertFalse("the .part file must be gone", File(expectedPath.absolutePath + ".part").exists())
    }

    @Test
    fun `a corrupted download is rejected, not cached`() = runTest {
        val payload = "tampered".toByteArray()
        server.enqueue(MockResponse().setBody(okio.Buffer().write(payload)))
        val root = temp.newFolder("assets-bad")
        val states = AssetDownloader(OkHttpClient(), root)
            .download(base() + "/assets/models/model_a/model_a.glb", "model_a/model_a.glb",
                "0".repeat(64), payload.size.toLong()).toList()

        val failed = states.filterIsInstance<DownloadState.Failed>().single()
        assertTrue(failed.message.contains("SHA-256"))
        assertFalse("a corrupt payload must not be left in the cache",
            File(root, "model_a/model_a.glb").exists())
    }
}
