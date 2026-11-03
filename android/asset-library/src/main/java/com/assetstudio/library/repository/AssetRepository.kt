package com.assetstudio.library.repository

import android.content.Context
import android.net.Uri
import com.assetstudio.core.model.*
import com.assetstudio.core.util.MetadataParser
import com.assetstudio.data.db.AssetDatabase
import com.assetstudio.data.db.CachedAssetEntity
import com.assetstudio.library.api.*
import com.assetstudio.library.download.AssetDownloader
import com.assetstudio.library.download.DownloadState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import okhttp3.OkHttpClient
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** Remote API + Room cache + SAF imports. Only verified exports are playable. */
class AssetRepository(
    private val context: Context, private val api: AssetStudioApi,
    private val http: OkHttpClient, private val baseUrl: suspend () -> String
) {
    private val dao = AssetDatabase.get(context).cachedAssetDao()
    private val downloader = AssetDownloader(context, http)

    fun cachedAssets(): Flow<List<CachedAssetEntity>> = dao.observeAll()
    fun cachedModels(): Flow<List<CachedAssetEntity>> = dao.observeByKind("model")
    fun searchCached(q: String): Flow<List<CachedAssetEntity>> = dao.search(q)
    fun totalCacheBytes(): Flow<Long> = dao.totalBytes().map { it ?: 0L }

    suspend fun health(): HealthDto = api.health()
    suspend fun assets(search: String = "", sort: String = "name"): List<AssetSummary> =
        api.assets(search = search, sort = sort).map { it.toDomain() }
    suspend fun compatibleAnimations(modelId: String): List<CompatibleAnimation> =
        api.compatibleAnimations(modelId).animations.map {
            CompatibleAnimation(it.id, it.name, it.category ?: "", it.durationSeconds.toFloat(),
                it.frameRate?.toFloat(), it.rootMotion, it.retargetStatus ?: "",
                it.targetCharacterId ?: "", it.compatible, it.playable, it.reason ?: "",
                it.downloadReference ?: "")
        }
    suspend fun modelDetail(id: String): ModelDetail {
        val d = JSONObject(api.model(id).toString())
        val m = JSONObject(runCatching { api.metadata(id).toString() }.getOrDefault("{}"))
        return ModelDetail(summary(d, "model"), MetadataParser.geometry(m.optJSONObject("geometry")),
            MetadataParser.materials(m.optJSONArray("materials")),
            MetadataParser.textures(m.optJSONArray("textures")),
            MetadataParser.skeleton(m.optJSONObject("skeleton")),
            MetadataParser.clips(m.optJSONObject("animations")?.optJSONArray("clips")),
            MetadataParser.validation(m, m.optJSONArray("issues")))
    }
    suspend fun animationDetail(id: String): AnimationDetail {
        val d = JSONObject(api.animation(id).toString())
        val m = JSONObject(runCatching { api.metadata(id).toString() }.getOrDefault("{}"))
        val ca = m.optJSONArray("compatibility")
        val records = (0 until (ca?.length() ?: 0)).map { i ->
            val c = ca!!.optJSONObject(i) ?: JSONObject()
            CompatibilityRecord(c.optString("target_character_id"), c.optString("target_name").takeIf { it.isNotEmpty() },
                c.optString("status"), c.optDouble("score",0.0).toFloat(),
                c.optDouble("role_coverage",0.0).toFloat(), c.optInt("mapped_bones"),
                c.strings("unmapped_roles"), c.strings("ambiguous_roles"), c.strings("reasons"))
        }
        return AnimationDetail(summary(d, "animation"),
            MetadataParser.clips(m.optJSONObject("animations")?.optJSONArray("clips")),
            m.optJSONObject("animations")?.optDouble("frame_rate")?.toFloat(),
            m.optBoolean("root_motion"), MetadataParser.skeleton(m.optJSONObject("source_skeleton")),
            records, d.optBoolean("playable", m.optString("retarget_status") in
                listOf("completed","passthrough_identical_skeleton") && m.optString("target_character_id").isNotEmpty()),
            d.optString("incompatibility_reason"), MetadataParser.validation(m, m.optJSONArray("issues")))
    }
    suspend fun report(kind: String, id: String): JsonObject = api.report(kind, id)
    suspend fun job(id: String): JsonObject = api.job(id)
    suspend fun latestRelease(): JsonObject = api.latestRelease()

    suspend fun download(asset: AssetSummary): Flow<DownloadState> {
        val url = baseUrl().trimEnd('/') + "/assets/${asset.id}/download"
        return downloader.download(url, asset.id, asset.processedSha256.takeIf { it.length == 64 }).map { s ->
            if (s is DownloadState.Finished) dao.upsert(CachedAssetEntity(
                asset.id, asset.kind, asset.name, asset.category, asset.sourceFormat,
                s.file.absolutePath, s.bytes, s.sha256, asset.sourcePath,
                asset.validationStatus, asset.humanoid, asset.targetCharacterId,
                asset.retargetStatus, "library", System.currentTimeMillis(), System.currentTimeMillis()))
            s
        }
    }

    /** Import a local GLB/glTF through the Storage Access Framework. */
    suspend fun importLocal(uri: Uri, displayName: String): Result<CachedAssetEntity> = withContext(Dispatchers.IO) {
        runCatching {
            val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                ?: error("Cannot open the selected file")
            require(bytes.size >= 20) { "File is too small to be a glTF asset" }
            val isGlb = bytes.copyOfRange(0, 4).contentEquals(byteArrayOf(0x67, 0x6C, 0x54, 0x46))
            val head = String(bytes.copyOfRange(0, minOf(bytes.size, 512)), Charsets.UTF_8)
            require(isGlb || head.contains("\"asset\"")) { "Only GLB / glTF files can be imported" }
            require(bytes.size <= AssetDownloader.MAX_FILE_BYTES) { "File exceeds the 500 MiB safety limit" }
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
            val sha = digest.joinToString("") { "%02x".format(it) }
            val id = "local_" + sha.take(16)
            val dir = File(context.filesDir, "assets").apply { mkdirs() }
            val target = File(dir, "$id.glb").apply { writeBytes(bytes) }
            val entity = CachedAssetEntity(id, "model", File(displayName).nameWithoutExtension,
                "imported", if (isGlb) "GLB" else "GLTF", target.absolutePath, bytes.size.toLong(),
                sha, uri.toString(), "NOT_PROCESSED", false, "", "", "import",
                System.currentTimeMillis(), System.currentTimeMillis())
            dao.upsert(entity); entity
        }
    }

    suspend fun deleteLocal(id: String) {
        dao.get(id)?.let { runCatching { File(it.localPath).delete() } }
        dao.delete(id)
    }
    suspend fun touch(id: String) = dao.touch(id, System.currentTimeMillis())

    private fun AssetSummaryDto.toDomain() = AssetSummary(id, kind, name, category ?: "",
        sourcePath ?: "", sourceFormat ?: "", processedPath = processedPath,
        processedBytes = processedBytes, processedSha256 = processedSha256 ?: "",
        validationStatus = validationStatus ?: "", humanoid = humanoid ?: false,
        retargetStatus = retargetStatus ?: "", targetCharacterId = targetCharacterId ?: "",
        updatedAt = updatedAt ?: "")

    private fun summary(d: JSONObject, kind: String) = AssetSummary(
        d.optString("id"), kind, d.optString("name"), d.optString("category"),
        d.optString("source_path"), d.optString("source_format"),
        d.optJSONObject("source")?.optString("sha256") ?: "",
        d.optJSONObject("processed")?.optString("path"),
        d.optJSONObject("processed")?.optLong("bytes") ?: 0,
        d.optJSONObject("processed")?.optString("sha256") ?: "",
        d.optJSONObject("validation")?.optString("status") ?: "",
        d.optJSONObject("skeleton")?.optBoolean("humanoid") ?: false,
        d.optString("retarget_status"), d.optString("target_character_id"),
        d.optString("timestamp"))

    private fun JSONObject.strings(k: String): List<String> =
        optJSONArray(k)?.let { a -> (0 until a.length()).map { a.optString(it) } } ?: emptyList()
}
