package com.assetstudio.app

import android.content.Context
import com.assetstudio.data.prefs.SettingsRepository
import com.assetstudio.library.api.ApiFactory
import com.assetstudio.library.repository.AssetRepository
import kotlinx.coroutines.flow.first

/** Manual dependency injection. No secrets are embedded; the API key comes from user settings. */
class AppContainer(private val context: Context) {
    val settings = SettingsRepository(context)

    private var currentBaseUrl: String = "https://asset-studio.example.invalid/api/v1/"
    private var currentApiKey: String = ""

    @Volatile private var repository: AssetRepository? = null

    fun repository(): AssetRepository = repository ?: synchronized(this) {
        repository ?: buildRepository(currentBaseUrl, currentApiKey).also { repository = it }
    }

    fun configure(baseUrl: String, apiKey: String) {
        if (baseUrl == currentBaseUrl && apiKey == currentApiKey && repository != null) return
        currentBaseUrl = baseUrl; currentApiKey = apiKey
        synchronized(this) { repository = buildRepository(baseUrl, apiKey) }
    }

    private fun buildRepository(baseUrl: String, apiKey: String): AssetRepository {
        val http = ApiFactory.buildClient(apiKey, BuildConfig.DEBUG)
        val api = ApiFactory.createWithClient(baseUrl, http)
        return AssetRepository(context, api, http) { currentBaseUrl }
    }

    suspend fun currentSettings() = settings.settings.first()
}
