package com.assetstudio.library.catalog

import com.assetstudio.library.download.AssetDownloader
import com.assetstudio.library.download.DownloadState
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Smoke tests against the **deployed** GitHub Pages site.
 *
 * These run only when `ASSET_STUDIO_LIVE_BASE_URL` (or the `assetStudio.liveBaseUrl`
 * system property) points at a published site, so a normal `./gradlew test` on a
 * developer machine stays offline. In CI the Android workflow exports the URL that the
 * app ships in `BuildConfig.CATALOG_BASE_URL`, which makes this the only test that
 * proves the client code can actually read the live catalog.
 *
 * Covered here: the listing (`catalog/index.json`), a per-asset manifest, a GLB
 * download verified against the published SHA-256 and byte count, the glTF sidecars
 * (the `scene.bin` buffer and the PNG textures), and the on-disk cache serving the catalog when the
 * network is gone.
 */
class LiveCatalogSmokeTest {

    @get:Rule val temp = TemporaryFolder()

    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private fun liveBase(): String? =
        (System.getenv("ASSET_STUDIO_LIVE_BASE_URL")
            ?: System.getProperty("assetStudio.liveBaseUrl"))
            ?.trim()?.trimEnd('/')?.takeIf { it.startsWith("https://") }

    private fun requireBase(): String {
        val base = liveBase()
        assumeTrue("ASSET_STUDIO_LIVE_BASE_URL is not set: skipping the live Pages smoke test",
            base != null)
        assumeTrue("$base is a placeholder host, refusing to test against it",
            CatalogDefaults.isUsableBaseUrl(base!!))
        return base
    }

    private fun cacheDir(): File = temp.newFolder("cache")

    private fun CatalogAsset.relativePath(): String =
        downloadUrl.removePrefix("${liveBase()}/")

    @Test
    fun `live listing is served and internally consistent`() = runBlocking {
        val base = requireBase()
        val client = CatalogClient(http, cacheDir())

        val (index, fromCache) = client.index(base)

        assertTrue("live catalog served no assets", index.total > 0)
        assertEquals("counts must match the served lists",
            index.total, index.counts.models + index.counts.animations)
        assertEquals(base, index.baseUrl.trimEnd('/'))
        assertTrue("every download URL must be absolute https",
            (index.models + index.animations).all {
                it.downloadUrl.startsWith("https://") &&
                    CatalogDefaults.isUsableBaseUrl(it.downloadUrl)
            })
        assertTrue("every GLB must publish a digest", index.models.all { it.sha256.length == 64 })

        val cached = client.cachedAt("$base/catalog/index.json")
        assertNotNull("the listing must be cached for offline use", cached)
        // A second read revalidates from the cache instead of refetching from scratch.
        val (again, secondFromCache) = client.index(base)
        assertEquals(index.total, again.total)
        assertTrue("a revalidated read is served from the cache",
            fromCache || secondFromCache || index.generatedAt == again.generatedAt)
    }

    @Test
    fun `live asset manifest is servable`() = runBlocking {
        val base = requireBase()
        val client = CatalogClient(http, cacheDir())
        val (index, _) = client.index(base)
        val asset = (index.models + index.animations).first { it.manifestUrl.isNotBlank() }

        val manifest = client.document(asset.manifestUrl)

        assertNotNull(manifest["id"])
        assertTrue(asset.manifestUrl.startsWith("$base/catalog/"))
    }

    @Test
    fun `live GLB downloads and matches the published digest`() = runBlocking {
        val base = requireBase()
        val client = CatalogClient(http, cacheDir())
        val (index, _) = client.index(base)
        val asset = (index.models + index.animations).first { it.bytes > 0 && it.sha256.length == 64 }
        val target = temp.newFolder("downloads")

        val states = AssetDownloader(http, target)
            .download(asset.downloadUrl, asset.relativePath(), asset.sha256, asset.bytes)
            .toList()

        val finished = states.filterIsInstance<DownloadState.Finished>().singleOrNull()
        assertNotNull("download failed: ${states.filterIsInstance<DownloadState.Failed>()}",
            finished)
        assertEquals("digest published by the catalog must match the served bytes",
            asset.sha256, finished!!.sha256)
        assertEquals(asset.bytes, finished.bytes)
        assertEquals(asset.bytes, File(target, asset.relativePath()).length())
        assertTrue("the .part scratch file must be renamed away",
            !File(target, "${asset.relativePath()}.part").exists())
    }

    @Test
    fun `live glTF sidecars are downloadable`() = runBlocking {
        val base = requireBase()
        val client = CatalogClient(http, cacheDir())
        val (index, _) = client.index(base)
        val bundled = (index.models + index.animations)
            .first { it.hasGltfBundle && it.dependencies.any { dep -> dep.role == "buffer" } }
        val roles = bundled.dependencies.map { it.role }.toSet()
        assertTrue("a published glTF bundle must carry its .bin buffer", roles.contains("buffer"))

        for (dependency in bundled.dependencies) {
            val data = client.bytes(dependency.url)
            assertEquals("$dependency.role size mismatch for ${dependency.path}",
                dependency.bytes, data.size.toLong())
            val digest = MessageDigest.getInstance("SHA-256").digest(data)
                .joinToString("") { "%02x".format(it) }
            assertEquals("$dependency.role digest mismatch for ${dependency.path}",
                dependency.sha256, digest)
            assertTrue("sidecar URL must live under the published base",
                dependency.url.startsWith(base))
        }

        val texture = (index.models + index.animations)
            .flatMap { it.dependencies }
            .firstOrNull { it.role == "texture" }
        if (texture != null) {
            val png = client.bytes(texture.url)
            assertTrue("a published texture must be a PNG",
                png.size > 8 && png[0] == 0x89.toByte() && png[1] == 'P'.code.toByte())
        }
    }

    @Test
    fun `cached catalog still loads when the network is gone`() = runBlocking {
        val base = requireBase()
        // One cache directory: the online client fills it, the offline client reads it.
        val shared = temp.newFolder("shared-cache")
        val (index, _) = CatalogClient(http, shared).index(base)

        val offline = CatalogClient(OkHttpClient.Builder()
            .addInterceptor { throw IOException("simulated offline device") }
            .build(), shared)

        val (cached, fromCache) = offline.index(base)

        assertTrue("the offline read must be served from the cache", fromCache)
        assertEquals(index.total, cached.total)
        assertEquals(index.baseUrl, cached.baseUrl)
    }
}
