package com.assetstudio.inspector

import com.assetstudio.core.model.*
import com.assetstudio.core.util.Formatting

object GeometryInspector {
    data class Row(val label: String, val value: String)
    fun rows(g: GeometryStats): List<Row> = listOf(
        Row("Mesh count", Formatting.number(g.meshCount)),
        Row("Primitive count", Formatting.number(g.primitiveCount)),
        Row("Vertex count", Formatting.number(g.vertexCount)),
        Row("Triangle count", Formatting.number(g.triangleCount)),
        Row("Index count", Formatting.number(g.indexCount)),
        Row("Material count", Formatting.number(g.materialCount)),
        Row("Texture count", Formatting.number(g.textureCount)),
        Row("UV sets", g.uvSets.joinToString().ifEmpty { "none" }),
        Row("Normals", if (g.hasNormals) "present" else "missing"),
        Row("Tangents", if (g.hasTangents) "present" else "missing"),
        Row("Morph targets", Formatting.number(g.morphTargetCount)),
        Row("Bounding box min", g.boundingBox?.min?.joinToString { "%.3f".format(it) } ?: "n/a"),
        Row("Bounding box max", g.boundingBox?.max?.joinToString { "%.3f".format(it) } ?: "n/a"),
        Row("Dimensions", g.dimensions.joinToString { "%.3f".format(it) }.ifEmpty { "n/a" })
    )
}

object MaterialInspector {
    data class State(val materials: List<MaterialInfo> = emptyList(),
                     val textures: List<TextureInfo> = emptyList(), val warnings: List<String> = emptyList())
    fun build(materials: List<MaterialInfo>, textures: List<TextureInfo>): State {
        val warnings = buildList {
            materials.forEach { m -> m.missingTextures.forEach { add("${m.name}: missing texture for $it") } }
            textures.filterNot { it.present }.forEach { add("Texture '${it.name}' is missing from the asset") }
        }
        return State(materials, textures, warnings)
    }
    fun rows(m: MaterialInfo): List<GeometryInspector.Row> = buildList {
        add(GeometryInspector.Row("Base colour", m.baseColorFactor.joinToString { "%.2f".format(it) }))
        add(GeometryInspector.Row("Metallic", "%.2f".format(m.metallicFactor)))
        add(GeometryInspector.Row("Roughness", "%.2f".format(m.roughnessFactor)))
        add(GeometryInspector.Row("Emissive", m.emissiveFactor.joinToString { "%.2f".format(it) }))
        add(GeometryInspector.Row("Alpha mode", m.alphaMode))
        add(GeometryInspector.Row("Alpha cutoff", "%.2f".format(m.alphaCutoff)))
        add(GeometryInspector.Row("Double sided", m.doubleSided.toString()))
        m.textures.forEach { (slot, ref) -> add(GeometryInspector.Row(slot, "texture #${ref.texture} (UV${ref.texCoord})")) }
        if (m.missingTextures.isNotEmpty()) add(GeometryInspector.Row("Missing textures", m.missingTextures.joinToString()))
    }
}

object ValidationInspector {
    fun headline(v: ValidationInfo): String = when (v.status) {
        "PASS" -> "PASS — no issues found"
        "WARNING" -> "WARNING — ${v.warning} warning(s)"
        "ERROR" -> "ERROR — ${v.error} error(s)"
        else -> "Validation status unavailable"
    }
    fun rows(v: ValidationInfo): List<GeometryInspector.Row> = listOf(
        GeometryInspector.Row("Status", v.status),
        GeometryInspector.Row("PASS checks", Formatting.number(v.pass)),
        GeometryInspector.Row("WARNING checks", Formatting.number(v.warning)),
        GeometryInspector.Row("ERROR checks", Formatting.number(v.error)),
        GeometryInspector.Row("Report", v.report.ifEmpty { "n/a" }))
}
