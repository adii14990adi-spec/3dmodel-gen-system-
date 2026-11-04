package com.assetstudio.library.download

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

/**
 * Streaming, cancellable, atomic download straight from GitHub Pages.
 *
 * The URL is whatever the catalog advertises (absolute https), so the downloader
 * needs no knowledge of a host. Files land under [root] at the catalog's own
 * relative path, which is what keeps a glTF's `scene.bin` and `textures/0.png`
 * references resolvable after the bundle is fetched.
 *
 * Integrity: the stream is hashed while it is written and compared with the digest
 * the catalog published; a mismatch deletes the partial file instead of leaving a
 * corrupt GLB in cache. The size declared by the server is also checked when the
 * catalog knows the expected length.
 */
class AssetDownloader(private val client: OkHttpClient, internal val root: File) {
    companion object {
        const val LARGE_FILE_BYTES = 100L * 1024 * 1024
        const val MAX_FILE_BYTES = 500L * 1024 * 1024
    }

    fun download(
        url: String,
        relativePath: String,
        expectedSha256: String? = null,
        expectedBytes: Long = 0
    ): Flow<DownloadState> = flow {
        withContext(Dispatchers.IO) {
            val target = File(root, relativePath)
            val part = File(target.parentFile, "${target.name}.part")
            target.parentFile?.mkdirs()
            try {
                val response = client.newCall(Request.Builder().url(url).build()).execute()
                if (!response.isSuccessful) {
                    emit(DownloadState.Failed("HTTP ${response.code} for $url")); return@withContext
                }
                val body = response.body ?: run {
                    emit(DownloadState.Failed("Empty response body from $url")); return@withContext
                }
                val total = body.contentLength()
                if (total > MAX_FILE_BYTES) {
                    emit(DownloadState.Failed("Asset exceeds the 500 MiB safety limit")); return@withContext
                }
                val digest = MessageDigest.getInstance("SHA-256")
                var read = 0L
                body.byteStream().use { input ->
                    part.outputStream().buffered().use { output ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            if (!coroutineContext.isActive) {
                                part.delete()
                                emit(DownloadState.Failed("Download cancelled")); return@withContext
                            }
                            val n = input.read(buffer)
                            if (n < 0) break
                            output.write(buffer, 0, n); digest.update(buffer, 0, n); read += n
                            emit(DownloadState.Progress(read, if (total > 0) total else read))
                        }
                    }
                }
                val sha = digest.digest().joinToString("") { "%02x".format(it) }
                if (expectedSha256 != null && sha != expectedSha256) {
                    part.delete()
                    emit(DownloadState.Failed("Integrity check failed: SHA-256 mismatch")); return@withContext
                }
                if (expectedBytes > 0 && read != expectedBytes) {
                    part.delete()
                    emit(DownloadState.Failed("Integrity check failed: expected $expectedBytes bytes, got $read"))
                    return@withContext
                }
                if (!part.renameTo(target)) {
                    part.delete()
                    emit(DownloadState.Failed("Could not finalise ${target.name}")); return@withContext
                }
                emit(DownloadState.Finished(target, sha, read))
            } catch (e: Exception) {
                part.delete()
                emit(DownloadState.Failed(e.message ?: "Download failed"))
            }
        }
    }
}
