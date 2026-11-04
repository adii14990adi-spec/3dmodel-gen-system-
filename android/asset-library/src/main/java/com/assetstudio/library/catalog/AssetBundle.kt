package com.assetstudio.library.catalog

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** One file of a downloadable asset, already resolved to its published URL. */
data class BundleFile(
    val url: String,
    val relativePath: String,
    val sha256: String?,
    val bytes: Long,
    val role: String
)

/**
 * Turns a catalog entry into the list of files the app has to fetch.
 *
 * A GLB is one file, so the bundle is a single download. A glTF is not: it points
 * at a `.bin` buffer and at PNG/JPEG textures by relative URI, and a client that
 * only downloads the `.gltf` gets an asset that cannot be displayed. The catalog
 * lists those sidecars as `dependencies` with absolute URLs; they are written back
 * at their published relative paths (`scene.bin`, `textures/0.png`) so the
 * document's own relative references keep working on disk.
 */
object AssetBundle {

    const val GLB = "glb"
    const val GLTF = "gltf"

    /**
     * Every file the app has to fetch for [asset].
     *
     * The GLB always comes first: it is the single-file form Filament renders, so a
     * download is usable for playback even if the extra glTF sidecars are dropped.
     * When the site published a glTF variant, its `.gltf`, `.bin` buffer and
     * texture files follow, written at the published relative paths so the
     * document's relative URIs resolve locally.
     */
    fun files(asset: CatalogAsset): List<BundleFile> {
        val kindDir = if (asset.kind == "animation") "animations" else "models"
        val bundle = mutableListOf(
            BundleFile(
                url = asset.downloadUrl,
                relativePath = "${asset.id}/${asset.id}.glb",
                sha256 = asset.sha256.takeIf { it.length == 64 },
                bytes = asset.bytes,
                role = GLB
            )
        )
        if (!asset.hasGltfBundle) return bundle
        bundle += BundleFile(
            url = asset.gltfUrl!!,
            relativePath = "${asset.id}/scene.gltf",
            sha256 = null,
            bytes = 0,
            role = GLTF
        )
        asset.dependencies.forEach { dependency ->
            bundle += BundleFile(
                url = dependency.url,
                relativePath = "${asset.id}/${sidecarName(dependency.path, kindDir, asset.id)}",
                sha256 = dependency.sha256.takeIf { it.length == 64 },
                bytes = dependency.bytes,
                role = dependency.role
            )
        }
        return bundle
    }

    /** The file the viewport opens for [asset] once its bundle is downloaded. */
    fun entryPath(asset: CatalogAsset): String = "${asset.id}/${asset.id}.glb"

    /**
     * `assets/models/<id>/textures/0.png` -> `textures/0.png`.
     *
     * Falls back to the file name for any path that is not laid out the way the
     * site builder writes it, so an unexpected catalog entry cannot escape the
     * asset directory.
     */
    fun sidecarName(path: String, kindDir: String, assetId: String): String {
        val normalised = path.trim().removePrefix("/")
        val prefix = "assets/$kindDir/$assetId/"
        val stripped = if (normalised.startsWith(prefix)) normalised.removePrefix(prefix)
        else normalised.substringAfterLast('/')
        val segments = stripped.split('/').filter { it.isNotEmpty() && it != "." && it != ".." }
        if (segments.isEmpty()) return "sidecar.bin"
        return segments.joinToString("/")
    }

    /** Sidecars a `.gltf` document requires, from its `buffers` and `images` URIs. */
    fun referencedUris(gltfJson: String): List<String> {
        val element = runCatching { CatalogJson.parseToJsonElement(gltfJson) }
            .getOrNull() as? JsonObject ?: return emptyList()
        val uris = mutableListOf<String>()
        listOf("buffers", "images").forEach { key ->
            (element[key] as? JsonArray)?.forEach { item ->
                val uri = ((item as? JsonObject)?.get("uri") as? JsonPrimitive)?.contentOrNull
                if (uri != null && !uri.startsWith("data:")) uris += uri
            }
        }
        return uris
    }
}
