package com.assetstudio.library.catalog

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/** One catalog document as it was served, plus whether it came from the disk cache. */
data class CatalogDocument(val body: String, val fromCache: Boolean, val url: String)

/**
 * Reads the GitHub Pages catalog over HTTPS with an on-disk revalidation cache.
 *
 * Everything is a plain file fetch: `catalog/index.json` (the listing) and
 * `catalog/<kind>/<id>.json` (per-asset manifest + metadata). Responses are cached
 * under `cacheDir/catalog` together with their ETag, so:
 *
 * * a normal refresh revalidates with `If-None-Match` and costs one 304;
 * * with no network the last good copy is returned (`fromCache = true`) and the
 *   library screen keeps listing real assets instead of going blank.
 *
 * No API key, no session, no server: GitHub Pages serves the same bytes to everyone.
 */
class CatalogClient(
    private val http: OkHttpClient,
    private val cacheDir: File,
    private val json: Json = CatalogJson
) {
    private val catalogCache = File(cacheDir, "catalog").apply { mkdirs() }

    /** Fetch `catalog/index.json`, revalidating the cached copy when possible. */
    suspend fun index(baseUrl: String, forceRefresh: Boolean = false): Pair<CatalogIndex, Boolean> {
        val url = "${CatalogDefaults.normaliseBaseUrl(baseUrl)}/catalog/index.json"
        val document = get(url, forceRefresh)
        val parsed = try {
            json.decodeFromString(CatalogIndex.serializer(), document.body)
        } catch (e: Exception) {
            throw IOException("The catalog at $url is not a valid asset-studio catalog: ${e.message}", e)
        }
        if (parsed.models.isEmpty() && parsed.animations.isEmpty()) {
            throw IOException("The catalog at $url is empty; run the Pages deployment workflow.")
        }
        return parsed to document.fromCache
    }

    /** Fetch a per-asset document (`catalog/<kind>/<id>.json`). */
    suspend fun document(url: String, forceRefresh: Boolean = false): JsonObject {
        val document = get(url, forceRefresh)
        return json.parseToJsonElement(document.body) as? JsonObject
            ?: throw IOException("$url is not a JSON object")
    }

    /** Small binary fetch (textures, `.bin` buffers) with a hard size limit. */
    suspend fun bytes(url: String, maxBytes: Long = 64L * 1024 * 1024): ByteArray =
        withContext(Dispatchers.IO) {
            val response = execute(Request.Builder().url(url).get().build())
            response.use {
                val body = it.body ?: throw IOException("Empty response body from $url")
                val declared = body.contentLength()
                if (declared > maxBytes) throw IOException("$url is larger than the ${maxBytes}B limit")
                val data = body.bytes()
                if (data.size > maxBytes) throw IOException("$url is larger than the ${maxBytes}B limit")
                data
            }
        }

    /** When the cached copy of [url] was last validated, or null when never fetched. */
    fun cachedAt(url: String): Long? = metaFile(url).takeIf { it.isFile }?.let { meta ->
        // The sidecar holds `fetched=<epoch millis>` and optionally `etag=…`; parsing the
        // whole file as a number (which is what the library screen used to do) always
        // failed and hid the "last refreshed" timestamp from the UI.
        meta.readLines()
            .firstOrNull { it.startsWith("fetched=") }
            ?.removePrefix("fetched=")?.trim()?.toLongOrNull()
    }

    private suspend fun get(url: String, forceRefresh: Boolean): CatalogDocument =
        withContext(Dispatchers.IO) {
            val bodyFile = bodyFile(url)
            val metaFile = metaFile(url)
            val cachedBody = bodyFile.takeIf { it.isFile }?.readText()
            val etag = metaFile.takeIf { it.isFile }?.let { meta ->
                meta.readLines().firstOrNull { it.startsWith("etag=") }?.removePrefix("etag=")
            }
            val request = Request.Builder().url(url).get().apply {
                header("Accept", "application/json")
                if (!forceRefresh && etag != null) header("If-None-Match", etag)
            }.build()
            try {
                execute(request).use { response ->
                    when {
                        response.code == 304 && cachedBody != null ->
                            CatalogDocument(cachedBody, fromCache = true, url = url)
                        response.isSuccessful -> {
                            val body = response.body?.string()
                                ?: throw IOException("Empty response body from $url")
                            bodyFile.parentFile?.mkdirs()
                            bodyFile.writeText(body)
                            val newEtag = response.header("ETag")
                            metaFile.writeText("fetched=${System.currentTimeMillis()}\n" +
                                (newEtag?.let { "etag=$it\n" } ?: ""))
                            CatalogDocument(body, fromCache = false, url = url)
                        }
                        cachedBody != null -> CatalogDocument(cachedBody, fromCache = true, url = url)
                        else -> throw IOException(
                            "GitHub Pages returned HTTP ${response.code} for $url. " +
                                "Check that Pages is enabled and the base URL is the published site.")
                    }
                }
            } catch (e: IOException) {
                if (cachedBody != null) return@withContext CatalogDocument(cachedBody, true, url)
                throw IOException(
                    "Cannot reach the asset catalog at $url (${e.message}). " +
                        "Downloaded assets keep working offline.", e)
            }
        }

    private fun execute(request: Request) = http.newCall(request).execute()

    private fun bodyFile(url: String) = File(catalogCache, "${digest(url)}.body")
    private fun metaFile(url: String) = File(catalogCache, "${digest(url)}.meta")

    private fun digest(value: String): String =
        MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") {
            "%02x".format(it)
        }.take(32)
}
