package com.assetstudio.library.download

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import kotlin.coroutines.coroutineContext

sealed interface DownloadState {
    data class Progress(val bytesRead: Long, val totalBytes: Long) : DownloadState
    data class Finished(val file: File, val sha256: String, val bytes: Long) : DownloadState
    data class Failed(val message: String) : DownloadState
}

/** Streaming, cancellable, atomic download with SHA-256 integrity verification. */
class AssetDownloader(private val context: Context, private val client: OkHttpClient) {
    companion object {
        const val LARGE_FILE_BYTES = 100L * 1024 * 1024
        const val MAX_FILE_BYTES = 500L * 1024 * 1024
    }

    fun download(url: String, assetId: String, expectedSha256: String? = null): Flow<DownloadState> = flow {
        withContext(Dispatchers.IO) {
            val dir = File(context.filesDir, "assets").apply { mkdirs() }
            val target = File(dir, "$assetId.glb")
            val part = File(dir, "$assetId.glb.part")
            try {
                val response = client.newCall(Request.Builder().url(url).build()).execute()
                if (!response.isSuccessful) { emit(DownloadState.Failed("HTTP ${response.code}")); return@withContext }
                val body = response.body ?: run { emit(DownloadState.Failed("Empty response body")); return@withContext }
                val total = body.contentLength()
                if (total > MAX_FILE_BYTES) { emit(DownloadState.Failed("Asset exceeds the 500 MiB safety limit")); return@withContext }
                val digest = MessageDigest.getInstance("SHA-256")
                var read = 0L
                body.byteStream().use { input -> part.outputStream().buffered().use { output ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        if (!coroutineContext.isActive) { emit(DownloadState.Failed("Download cancelled")); return@withContext }
                        val n = input.read(buf)
                        if (n < 0) break
                        output.write(buf, 0, n); digest.update(buf, 0, n); read += n
                        emit(DownloadState.Progress(read, if (total > 0) total else read))
                    }
                } }
                val sha = digest.digest().joinToString("") { "%02x".format(it) }
                if (expectedSha256 != null && sha != expectedSha256) {
                    part.delete(); emit(DownloadState.Failed("Integrity check failed: SHA-256 mismatch")); return@withContext
                }
                if (!part.renameTo(target)) { part.delete(); emit(DownloadState.Failed("Could not finalise file")); return@withContext }
                emit(DownloadState.Finished(target, sha, read))
            } catch (e: Exception) {
                part.delete(); emit(DownloadState.Failed(e.message ?: "Download failed"))
            }
        }
    }
}
