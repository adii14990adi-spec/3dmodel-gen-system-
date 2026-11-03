package com.assetstudio.library.api

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.http.*
import java.util.concurrent.TimeUnit

@Serializable
data class AssetSummaryDto(
    val id: String, val kind: String, val name: String, val category: String? = null,
    @SerialName("source_path") val sourcePath: String? = null,
    @SerialName("source_format") val sourceFormat: String? = null,
    @SerialName("processed_path") val processedPath: String? = null,
    @SerialName("processed_bytes") val processedBytes: Long = 0,
    @SerialName("processed_sha256") val processedSha256: String? = null,
    @SerialName("validation_status") val validationStatus: String? = null,
    val humanoid: Boolean? = null,
    @SerialName("retarget_status") val retargetStatus: String? = null,
    @SerialName("target_character_id") val targetCharacterId: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null
)

@Serializable
data class CompatibleAnimationDto(
    val id: String, val name: String, val category: String? = null,
    @SerialName("duration_seconds") val durationSeconds: Double = 0.0,
    @SerialName("frame_rate") val frameRate: Double? = null,
    @SerialName("root_motion") val rootMotion: Boolean = false,
    @SerialName("retarget_status") val retargetStatus: String? = null,
    @SerialName("target_character_id") val targetCharacterId: String? = null,
    val compatible: Boolean = false, val playable: Boolean = false,
    val reason: String? = null,
    @SerialName("download_reference") val downloadReference: String? = null
)

@Serializable
data class CompatibleAnimationsDto(val count: Int = 0, val animations: List<CompatibleAnimationDto> = emptyList())

@Serializable
data class HealthDto(
    val status: String, val version: String, val authentication: String,
    @SerialName("storage_driver") val storageDriver: String,
    val validator: String = "", val assets: Map<String, Int> = emptyMap()
)

interface AssetStudioApi {
    @GET("health") suspend fun health(): HealthDto
    @GET("assets") suspend fun assets(@Query("kind") kind: String? = null,
        @Query("search") search: String? = null, @Query("sort") sort: String? = null,
        @Query("limit") limit: Int = 200): List<AssetSummaryDto>
    @GET("assets/{id}") suspend fun asset(@Path("id") id: String): JsonObject
    @GET("assets/{id}/metadata") suspend fun metadata(@Path("id") id: String): JsonObject
    @GET("models/{id}") suspend fun model(@Path("id") id: String): JsonObject
    @GET("animations/{id}") suspend fun animation(@Path("id") id: String): JsonObject
    @GET("models/{id}/compatible-animations") suspend fun compatibleAnimations(@Path("id") id: String): CompatibleAnimationsDto
    @GET("processing-jobs/{jobId}") suspend fun job(@Path("jobId") jobId: String): JsonObject
    @GET("releases/latest") suspend fun latestRelease(): JsonObject
    @GET("reports/{kind}/{id}") suspend fun report(@Path("kind") kind: String, @Path("id") id: String): JsonObject
    @POST("assets/{id}/signed-url") suspend fun signedUrl(@Path("id") id: String): JsonObject
}

object ApiFactory {
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true; isLenient = true }

    fun buildClient(apiKey: String = "", debug: Boolean = false): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS)
            .callTimeout(120, TimeUnit.SECONDS).retryOnConnectionFailure(true)
            .addInterceptor { chain ->
                val request = chain.request().newBuilder()
                    .addHeader("Accept", "application/json")
                    .apply { if (apiKey.isNotBlank()) addHeader("X-API-Key", apiKey) }
                    .build()
                chain.proceed(request)
            }
            .apply { if (debug) addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }) }
            .build()

    fun createWithClient(baseUrl: String, client: OkHttpClient): AssetStudioApi {
        val url = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        return Retrofit.Builder().baseUrl(url).client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build().create(AssetStudioApi::class.java)
    }
}
