package com.assetstudio.library.catalog

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The bundle plan decides which files a download touches.
 *
 * The case that breaks naive static hosting is the glTF one: a `scene.gltf` alone
 * is useless without its `.bin` buffer and PNG textures, and those must land at the
 * same relative paths the document references.
 */
class AssetBundleTest {

    private fun asset(
        id: String = "model_a",
        kind: String = "model",
        gltf: String? = "https://owner.github.io/repo/assets/models/model_a/scene.gltf",
        dependencies: List<CatalogDependency> = listOf(
            CatalogDependency("buffer", "assets/models/model_a/scene.bin",
                "https://owner.github.io/repo/assets/models/model_a/scene.bin", 4, "b".repeat(64)),
            CatalogDependency("texture", "assets/models/model_a/textures/0.png",
                "https://owner.github.io/repo/assets/models/model_a/textures/0.png", 3, "c".repeat(64))
        )
    ) = CatalogAsset(
        id = id, kind = kind, name = "Scout", bytes = 12, sha256 = "a".repeat(64),
        downloadUrl = "https://owner.github.io/repo/assets/models/$id/$id.glb",
        manifestUrl = "https://owner.github.io/repo/catalog/models/$id.json",
        gltfUrl = gltf, dependencies = dependencies
    )

    @Test
    fun `a glb asset is a single request`() {
        val files = AssetBundle.files(asset(gltf = null, dependencies = emptyList()))
        assertEquals(1, files.size)
        assertEquals(AssetBundle.GLB, files[0].role)
        assertEquals("model_a/model_a.glb", files[0].relativePath)
        assertEquals("model_a/model_a.glb", AssetBundle.entryPath(asset(gltf = null)))
    }

    @Test
    fun `a gltf asset pulls the glb, the document and every sidecar`() {
        val files = AssetBundle.files(asset())
        assertEquals(4, files.size)
        // The GLB comes first: playback must work even if the extra files fail.
        assertEquals(AssetBundle.GLB, files[0].role)
        assertEquals(AssetBundle.GLTF, files[1].role)
        assertEquals(listOf("model_a/scene.gltf", "model_a/scene.bin", "model_a/textures/0.png"),
            files.drop(1).map { it.relativePath })
        assertEquals(listOf(null, "b".repeat(64), "c".repeat(64)),
            files.drop(1).map { it.sha256 })
        // Sidecars are written beneath the asset directory, never outside it.
        assertTrue(files.all { it.relativePath.startsWith("model_a/") })
    }

    @Test
    fun `sidecar paths keep their subfolders and cannot escape the asset directory`() {
        assertEquals("textures/0.png",
            AssetBundle.sidecarName("assets/models/model_a/textures/0.png", "models", "model_a"))
        assertEquals("scene.bin",
            AssetBundle.sidecarName("assets/animations/anim_a/scene.bin", "animations", "anim_a"))
        assertEquals("stray.bin",
            AssetBundle.sidecarName("assets/models/other/x/stray.bin", "models", "model_a"))
        assertFalse(AssetBundle.sidecarName("../../etc/passwd", "models", "model_a")
            .contains(".."))
    }

    @Test
    fun `gltf sidecar uris are discovered from buffers and images`() {
        val document = """
            {"asset":{"version":"2.0"},
             "buffers":[{"uri":"scene.bin","byteLength":4}],
             "images":[{"uri":"textures/0.png"},{"uri":"data:image/png;base64,AAAA"}]}
        """.trimIndent()
        assertEquals(listOf("scene.bin", "textures/0.png"), AssetBundle.referencedUris(document))
    }

    @Test
    fun `the default catalog url is a real https pages site, not a placeholder`() {
        assertTrue("build config must carry the Pages URL: ${CatalogDefaults.DEFAULT_BASE_URL}",
            CatalogDefaults.DEFAULT_BASE_URL.startsWith("https://"))
        assertTrue(CatalogDefaults.DEFAULT_BASE_URL.endsWith(".github.io/3dmodel-gen-system-"))
        CatalogDefaults.PLACEHOLDERS.forEach { placeholder ->
            assertFalse("placeholder $placeholder leaked into the default URL",
                CatalogDefaults.DEFAULT_BASE_URL.contains(placeholder))
        }
        assertTrue(CatalogDefaults.isUsableBaseUrl(CatalogDefaults.DEFAULT_BASE_URL))
        assertFalse(CatalogDefaults.isUsableBaseUrl("https://" + "example" + "." + "invalid" + "/api"))
        assertFalse(CatalogDefaults.isUsableBaseUrl("http://example.github.io/repo"))
        assertEquals("https://x.github.io/y", CatalogDefaults.normaliseBaseUrl(" https://x.github.io/y/ "))
    }
}
