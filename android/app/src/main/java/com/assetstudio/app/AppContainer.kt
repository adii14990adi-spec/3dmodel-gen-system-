package com.assetstudio.app

import android.content.Context
import com.assetstudio.data.prefs.SettingsRepository
import com.assetstudio.library.catalog.CatalogClient
import com.assetstudio.library.catalog.CatalogDefaults
import com.assetstudio.library.repository.AssetRepository
import kotlinx.coroutines.flow.first
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Manual dependency injection for the static-hosting client.
 *
 * The app talks to GitHub Pages only: [CatalogClient] fetches the JSON documents
 * under `catalog/` and
 * the assets from the published site. There is no API key and no API server — the
 * base URL defaults to the repository's Pages project site and can be overridden in
 * Settings (for a fork, for example).
 */
class AppContainer(private val context: Context) {
    val settings = SettingsRepository(context, CatalogDefaults.DEFAULT_BASE_URL)

    private var currentBaseUrl: String = CatalogDefaults.DEFAULT_BASE_URL

    @Volatile private var repository: AssetRepository? = null

    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .callTimeout(120, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .addInterceptor { chain ->
            chain.proceed(
                chain.request().newBuilder()
                    .addHeader("Accept", "application/json")
                    .build()
            )
        }
        .apply {
            if (BuildConfig.DEBUG) {
                addInterceptor(HttpLoggingInterceptor().apply {
                    level = HttpLoggingInterceptor.Level.BASIC
                })
            }
        }
        .build()

    fun repository(): AssetRepository = repository ?: synchronized(this) {
        repository ?: buildRepository(currentBaseUrl).also { repository = it }
    }

    /** Point the catalog at another Pages site (a fork, or a local preview build). */
    fun configure(baseUrl: String) {
        val normalised = CatalogDefaults.normaliseBaseUrl(
            baseUrl.ifBlank { CatalogDefaults.DEFAULT_BASE_URL })
        if (normalised == currentBaseUrl && repository != null) return
        currentBaseUrl = normalised
        synchronized(this) { repository = buildRepository(normalised) }
    }

    private fun buildRepository(baseUrl: String): AssetRepository {
        val client = CatalogClient(http, File(context.cacheDir, "catalog-http"))
        return AssetRepository(context, client, http) { currentBaseUrl }
    }

    suspend fun currentSettings() = settings.settings.first()
}
