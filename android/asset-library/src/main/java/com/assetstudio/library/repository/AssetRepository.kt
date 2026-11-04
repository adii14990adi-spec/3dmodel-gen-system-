package com.assetstudio.library.repository

import android.content.Context
import android.net.Uri
import com.assetstudio.core.model.*
import com.assetstudio.core.util.MetadataParser
import com.assetstudio.data.db.AssetDatabase
import com.assetstudio.data.db.CachedAssetEntity
import com.assetstudio.data.db.CatalogAssetEntity
import com.assetstudio.library.catalog.AssetBundle
import com.assetstudio.library.catalog.CatalogAsset
import com.assetstudio.library.catalog.CatalogClient
import com.assetstudio.library.catalog.CatalogCounts
import com.assetstudio.library.catalog.CatalogDependency
import com.assetstudio.library.catalog.CatalogRefresh
import com.assetstudio.library.catalog.HealthDto
import com.assetstudio.library.download.AssetDownloader
import com.assetstudio.library.download.DownloadState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/**
 * Offline-first repository over the **static** GitHub Pages catalog.
 *
 * There is no API server in this architecture. Metadata comes from
 * `catalog/index.json` and `catalog/<kind>/<id>.json`; geometry comes from
 * `assets/<kind>/<id>/…`. Every successful refresh is written to Room, so the
 * library, the detail screens, downloads and previously downloaded GLBs all keep
 * working with no network at all. Local imports through the Storage Access
 * Framework never leave the device and are never overwritten by a refresh.
 */
class AssetRepository(
    private val context: Context,
    private val client: CatalogClient,
    private val http: OkHttpClient,
    private val baseUrl: () -> String
) {
    private val dao = AssetDatabase.get(context).cachedAssetDao()
    private val catalogDao = AssetDatabase.get(context).catalogAssetDao()
    private val downloadsRoot = File(context.filesDir, "assets")
    private val downloader = AssetDownloader(http, downloadsRoot)
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    // ------------------------------------------------------------------ cache --
    fun cachedAssets(): Flow<List<CachedAssetEntity>> = dao.observeAll()
    fun cachedModels(): Flow<List<CachedAssetEntity>> = dao.observeByKind("model")
    fun searchCached(q: String): Flow<List<CachedAssetEntity>> = dao.search(q)
    fun totalCacheBytes(): Flow<Long> = dao.totalBytes().map { it ?: 0L }

    /** Offline listing of everything the published catalog advertises. */
    fun catalogAssets(): Flow<List<CatalogAssetEntity>> = catalogDao.observeAll()
    fun catalogByKind(kind: String): Flow<List<CatalogAssetEntity>> = catalogDao.observeByKind(kind)
    fun catalogCount(): Flow<Int> = catalogDao.count()
    fun lastRefresh(): Flow<Long?> = catalogDao.lastFetchedAt()

    // ------------------------------------------------------------- metadata ----
    /**
     * Re-read `catalog/index.json` and store it. Returns the remote counts so the
     * caller can say "7 models / 83 animations" without inventing numbers.
     */
    suspend fun refreshCatalog(force: Boolean = true): CatalogRefresh = withContext(Dispatchers.IO) {
        val (index, fromCache) = client.index(baseUrl(), forceRefresh = force)
        val stamp = System.currentTimeMillis()
        catalogDao.replaceKind("models", index.models.map { it.toEntity(stamp) })
        catalogDao.replaceKind("animations", index.animations.map { it.toEntity(stamp) })
        CatalogRefresh(
            counts = CatalogCounts(index.models.size, index.animations.size),
            generatedAt = index.generatedAt,
            baseUrl = index.baseUrl.ifBlank { baseUrl() },
            fromCache = fromCache,
            skipped = index.skipped.size
        )
    }

    /** Catalog state for the header: derived from the cached catalog, no server call. */
    suspend fun health(): HealthDto = withContext(Dispatchers.IO) {
        val rows = catalogDao.byKind("models") + catalogDao.byKind("animations")
        HealthDto(
            status = if (rows.isEmpty()) "empty" else "ok",
            version = "static-catalog",
            authentication = "none (public static files)",
            storageDriver = "github-pages",
            validator = "${rows.count { it.validationStatus.equals("PASS", true) }}/${rows.size} validated",
            assets = mapOf("models" to rows.count { it.kind == "model" },
                           "animations" to rows.count { it.kind == "animation" })
        )
    }

    /** Assets for the library list, from the offline catalog copy. */
    suspend fun assets(search: String = "", sort: String = "name"): List<AssetSummary> =
        withContext(Dispatchers.IO) {
            val rows = catalogDao.byKind("models") + catalogDao.byKind("animations")
            val filtered = rows.filter { row ->
                search.isBlank() ||
                    row.name.contains(search, ignoreCase = true) ||
                    row.id.contains(search, ignoreCase = true) ||
                    row.category.contains(search, ignoreCase = true)
            }
            val sorted = when (sort) {
                "category" -> filtered.sortedWith(compareBy({ it.category }, { it.name }))
                "bytes" -> filtered.sortedByDescending { it.bytes }
                else -> filtered.sortedBy { it.name.lowercase() }
            }
            sorted.map { it.toSummary() }
        }

    suspend fun compatibleAnimations(modelId: String): List<CompatibleAnimation> =
        withContext(Dispatchers.IO) {
            catalogDao.byKind("animations")
                .filter { it.targetCharacterId == modelId }
                .map {
                    CompatibleAnimation(
                        it.id, it.name, it.category, it.durationSeconds.toFloat(), null,
                        false, it.retargetStatus, it.targetCharacterId,
                        compatible = true,
                        playable = it.retargetStatus in PLAYABLE_RETARGET_STATUSES,
                        reason = "", downloadReference = it.downloadUrl
                    )
                }
        }

    suspend fun modelDetail(id: String): ModelDetail = withContext(Dispatchers.IO) {
        val document = documentFor(id)
        val metadata = document.optJSONObject("metadata") ?: JSONObject()
        ModelDetail(
            summary(document.toSummary("model", id)),
            MetadataParser.geometry(metadata.optJSONObject("geometry")),
            MetadataParser.materials(metadata.optJSONArray("materials")),
            MetadataParser.textures(metadata.optJSONArray("textures")),
            MetadataParser.skeleton(metadata.optJSONObject("skeleton")),
            MetadataParser.clips(metadata.optJSONObject("animations")?.optJSONArray("clips")
                ?: document.optJSONObject("animations")?.optJSONArray("clips")),
            validation(document, metadata)
        )
    }

    suspend fun animationDetail(id: String): AnimationDetail = withContext(Dispatchers.IO) {
        val document = documentFor(id)
        val metadata = document.optJSONObject("metadata") ?: JSONObject()
        val compatibility = metadata.optJSONArray("compatibility") ?: JSONArray()
        val records = (0 until compatibility.length()).map { index ->
            val entry = compatibility.optJSONObject(index) ?: JSONObject()
            CompatibilityRecord(
                entry.optString("target_character_id"), entry.optString("target_name").ifBlank { null },
                entry.optString("status"), entry.optDouble("score", 0.0).toFloat(),
                entry.optDouble("role_coverage", 0.0).toFloat(), entry.optInt("mapped_bones"),
                entry.strings("unmapped_roles"), entry.strings("ambiguous_roles"),
                entry.strings("reasons")
            )
        }
        val retargetStatus = metadata.optString("retarget_status")
            .ifBlank { document.optString("retarget_status") }
        val target = metadata.optString("target_character_id")
            .ifBlank { document.optString("target_character_id") }
        AnimationDetail(
            document.toSummary("animation", id),
            MetadataParser.clips(metadata.optJSONObject("animations")?.optJSONArray("clips")
                ?: document.optJSONObject("animations")?.optJSONArray("clips")),
            metadata.optJSONObject("animations")?.optDouble("frame_rate")?.toFloat(),
            metadata.optBoolean("root_motion", document.optBoolean("root_motion", false)),
            MetadataParser.skeleton(metadata.optJSONObject("source_skeleton")
                ?: metadata.optJSONObject("skeleton")),
            records,
            retargetStatus in PLAYABLE_RETARGET_STATUSES && target.isNotEmpty(),
            document.optString("incompatibility_reason"),
            validation(document, metadata)
        )
    }

    /** Validation report for the detail screens, from the published manifest. */
    suspend fun report(kind: String, id: String): JsonObject = withContext(Dispatchers.IO) {
        val document = documentFor(id)
        json.parseToJsonElement(document.toString()) as? JsonObject ?: JsonObject(emptyMap())
    }

    // ------------------------------------------------------------ downloads ----
    /**
     * Download an asset from the catalog. A glTF asset pulls every published
     * sidecar (`.bin`, textures) as well; a GLB is a single request. Progress is
     * aggregated over the whole bundle so the UI stays honest about total work.
     */
    fun download(asset: AssetSummary): Flow<DownloadState> = flow {
        val intent = intentFor(asset.id, asset.kind)
        if (intent == null) {
            emit(DownloadState.Failed("${asset.name} is not in the published catalog; refresh it first."))
            return@flow
        }
        val plan = AssetBundle.files(intent)
        val totalBytes = plan.sumOf { it.bytes }.takeIf { it > 0 } ?: 0L
        var completed = 0L
        var entryFile: File? = null       // the GLB the viewport opens
        var entrySha = ""
        for (file in plan) {
            var finished = false
            downloader.download(file.url, file.relativePath, file.sha256, file.bytes)
                .collect { state ->
                    when (state) {
                        is DownloadState.Progress -> emit(
                            DownloadState.Progress(completed + state.bytesRead,
                                if (totalBytes > 0) totalBytes else completed + state.totalBytes))
                        is DownloadState.Failed -> {
                            emit(state); finished = true
                        }
                        is DownloadState.Finished -> {
                            completed += state.bytes
                            if (file.role == AssetBundle.GLB) {
                                entryFile = state.file
                                entrySha = state.sha256
                            }
                        }
                    }
                }
            if (finished) return@flow
        }
        val glb = entryFile ?: File(downloadsRoot, AssetBundle.entryPath(intent)).takeIf { it.isFile }
            ?: run {
                emit(DownloadState.Failed("Download produced no GLB")); return@flow
            }
        dao.upsert(CachedAssetEntity(
            intent.id, intent.kind, intent.name, intent.category, intent.sourceFormat,
            glb.absolutePath, completed,
            entrySha.ifBlank { intent.sha256 }, intent.downloadUrl,
            intent.validationStatus, intent.humanoid, intent.targetCharacterId,
            intent.retargetStatus, "catalog", System.currentTimeMillis(), System.currentTimeMillis()
        ))
        emit(DownloadState.Finished(glb, entrySha.ifBlank { intent.sha256 }, completed))
    }

    /** Path of the local copy of [id] when it has already been downloaded. */
    suspend fun localPathFor(id: String): String? = withContext(Dispatchers.IO) {
        dao.get(id)?.localPath?.takeIf { File(it).isFile }
    }

    // --------------------------------------------------------- local import ----
    /** Import a local GLB/glTF through the Storage Access Framework. */
    suspend fun importLocal(uri: Uri, displayName: String): Result<CachedAssetEntity> =
        withContext(Dispatchers.IO) {
            runCatching {
                val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    ?: error("Cannot open the selected file")
                require(bytes.size >= 20) { "File is too small to be a glTF asset" }
                val isGlb = bytes.copyOfRange(0, 4)
                    .contentEquals(byteArrayOf(0x67, 0x6C, 0x54, 0x46))
                val head = String(bytes.copyOfRange(0, minOf(bytes.size, 512)), Charsets.UTF_8)
                require(isGlb || head.contains("\"asset\"")) { "Only GLB / glTF files can be imported" }
                require(bytes.size <= AssetDownloader.MAX_FILE_BYTES) {
                    "File exceeds the 500 MiB safety limit"
                }
                val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
                val sha = digest.joinToString("") { "%02x".format(it) }
                val id = "local_" + sha.take(16)
                downloadsRoot.mkdirs()
                val target = File(downloadsRoot, "$id.${if (isGlb) "glb" else "gltf"}")
                    .apply { writeBytes(bytes) }
                val entity = CachedAssetEntity(
                    id, "model", File(displayName).nameWithoutExtension, "imported",
                    if (isGlb) "GLB" else "GLTF", target.absolutePath, bytes.size.toLong(), sha,
                    uri.toString(), "NOT_PROCESSED", false, "", "", "import",
                    System.currentTimeMillis(), System.currentTimeMillis())
                dao.upsert(entity)
                entity
            }
        }

    suspend fun deleteLocal(id: String) {
        dao.get(id)?.let { cached ->
            runCatching {
                if (cached.origin == "catalog") {
                    // Catalog bundles live in their own directory (GLB + glTF sidecars).
                    File(downloadsRoot, id).deleteRecursively()
                } else {
                    File(cached.localPath).delete()
                }
            }
        }
        dao.delete(id)
    }

    suspend fun touch(id: String) = dao.touch(id, System.currentTimeMillis())

    // ---------------------------------------------------------------- helpers --
    private suspend fun documentFor(id: String): JSONObject {
        val entity = catalogDao.get(id)
            ?: throw IllegalStateException(
                "Asset $id is not in the cached catalog; refresh the catalog first.")
        val document = client.document(entity.manifestUrl)
        return JSONObject(document.toString())
    }

    private suspend fun intentFor(id: String, kind: String): CatalogAsset? {
        val entity = catalogDao.get(id) ?: return null
        val dependencies = runCatching {
            json.decodeFromString(kotlinx.serialization.builtins.ListSerializer(
                CatalogDependency.serializer()), entity.dependenciesJson)
        }.getOrDefault(emptyList())
        return CatalogAsset(
            id = entity.id, kind = if (entity.kind == "animation") "animation" else "model",
            name = entity.name, category = entity.category, sourceFormat = entity.sourceFormat,
            format = entity.format, bytes = entity.bytes, sha256 = entity.sha256,
            validationStatus = entity.validationStatus, humanoid = entity.humanoid,
            targetCharacterId = entity.targetCharacterId, retargetStatus = entity.retargetStatus,
            clipCount = entity.clipCount, durationSeconds = entity.durationSeconds,
            updatedAt = entity.updatedAt, downloadUrl = entity.downloadUrl,
            manifestUrl = entity.manifestUrl, gltfUrl = entity.gltfUrl, dependencies = dependencies
        )
    }

    private fun CatalogAsset.toEntity(stamp: Long) = CatalogAssetEntity(
        id = id, kind = kind, name = name, category = category, sourceFormat = sourceFormat,
        format = format, bytes = bytes, sha256 = sha256, validationStatus = validationStatus,
        humanoid = humanoid, targetCharacterId = targetCharacterId,
        retargetStatus = retargetStatus, clipCount = clipCount, durationSeconds = durationSeconds,
        updatedAt = updatedAt, downloadUrl = downloadUrl, manifestUrl = manifestUrl,
        gltfUrl = gltfUrl,
        dependenciesJson = Json.encodeToString(
            kotlinx.serialization.builtins.ListSerializer(CatalogDependency.serializer()),
            dependencies),
        fetchedAt = stamp
    )

    private fun CatalogAssetEntity.toSummary() = AssetSummary(
        id, kind, name, category, sourcePath = downloadUrl, sourceFormat = sourceFormat,
        processedPath = downloadUrl, processedBytes = bytes, processedSha256 = sha256,
        validationStatus = validationStatus, humanoid = humanoid,
        retargetStatus = retargetStatus, targetCharacterId = targetCharacterId, updatedAt = updatedAt
    )

    private fun JSONObject.toSummary(kind: String, fallbackId: String): AssetSummary {
        val processed = optJSONObject("processed") ?: JSONObject()
        val skeleton = optJSONObject("skeleton") ?: JSONObject()
        val assets = optJSONObject("assets") ?: JSONObject()
        val glb = assets.optJSONObject("glb") ?: JSONObject()
        return AssetSummary(
            id = optString("id", fallbackId), kind = kind, name = optString("name"),
            category = optString("category"),
            sourcePath = optJSONObject("source")?.optString("path") ?: "",
            sourceFormat = optJSONObject("source")?.optString("format") ?: "",
            sourceSha256 = optJSONObject("source")?.optString("sha256") ?: "",
            processedPath = glb.optString("url").ifBlank { processed.optString("path") },
            processedBytes = glb.optLong("bytes", processed.optLong("bytes")),
            processedSha256 = glb.optString("sha256").ifBlank { processed.optString("sha256") },
            validationStatus = optJSONObject("validation")?.optString("status") ?: "",
            humanoid = skeleton.optBoolean("humanoid"),
            retargetStatus = optString("retarget_status"),
            targetCharacterId = optString("target_character_id"),
            updatedAt = optString("timestamp")
        )
    }

    private fun validation(document: JSONObject, metadata: JSONObject): ValidationInfo {
        val validation = document.optJSONObject("validation") ?: JSONObject()
        val issues = metadata.optJSONArray("issues") ?: document.optJSONArray("issues") ?: JSONArray()
        val checks = (0 until issues.length()).map { index ->
            val issue = issues.optJSONObject(index) ?: JSONObject()
            ValidationCheck(
                issue.optString("severity"), issue.optString("code"),
                issue.optString("description"), issue.optString("asset", document.optString("id")),
                issue.optString("location"), issue.optString("suggested_fix")
            )
        }
        val summary = document.optJSONObject("validation")?.optJSONObject("summary")
        return ValidationInfo(
            status = validation.optString("status"),
            pass = summary?.optInt("PASS") ?: checks.count { it.severity.equals("INFO", true) },
            warning = summary?.optInt("WARNING") ?: checks.count { it.severity.equals("WARNING", true) },
            error = summary?.optInt("ERROR") ?: checks.count { it.severity.equals("ERROR", true) },
            report = validation.optString("report"),
            checks = checks
        )
    }

    private fun JSONObject.strings(key: String): List<String> =
        optJSONArray(key)?.let { array -> (0 until array.length()).map { array.optString(it) } }
            ?: emptyList()

    companion object {
        val PLAYABLE_RETARGET_STATUSES = setOf("completed", "passthrough_identical_skeleton")
    }
}
