package com.assetstudio.renderer

import org.json.JSONArray
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class DebugLine(val start: FloatArray, val end: FloatArray, val colour: FloatArray)

/**
 * Builds real glTF 2.0 GLBs containing LINE/TRIANGLES primitives for viewport
 * overlays (grid, XYZ axes, ground, skeleton bones). Filament gltfio loads them
 * like any other asset: overlays are genuine 3D geometry, not a 2D canvas mock.
 */
object DebugGlbBuilder {
    private fun align4(n: Int) = (n + 3) and 3.inv()
    private fun floats(v: List<Float>) = ByteBuffer.allocate(v.size * 4).order(ByteOrder.LITTLE_ENDIAN)
        .apply { v.forEach { putFloat(it) } }.array()
    private fun shorts(v: List<Int>) = ByteBuffer.allocate(v.size * 2).order(ByteOrder.LITTLE_ENDIAN)
        .apply { v.forEach { putShort(it.toShort()) } }.array()

    private fun extrema(values: List<Float>, stride: Int, min: Boolean) = JSONArray().apply {
        for (axis in 0 until stride) {
            val col = values.filterIndexed { i, _ -> i % stride == axis }
            put((if (col.isEmpty()) 0f else (if (min) col.minOrNull()!! else col.maxOrNull()!!)).toDouble())
        }
    }

    private fun buildGeometry(positions: List<Float>, colours: List<Float>,
                              indices: List<Int>, mode: Int): ByteBuffer {
        val p = floats(positions); val c = floats(colours); val idx = shorts(indices)
        val bin = ByteArray(align4(p.size) + align4(c.size) + align4(idx.size))
        var off = 0
        System.arraycopy(p, 0, bin, off, p.size); val pOff = off; off += align4(p.size)
        System.arraycopy(c, 0, bin, off, c.size); val cOff = off; off += align4(c.size)
        System.arraycopy(idx, 0, bin, off, idx.size); val iOff = off
        val json = JSONObject().apply {
            put("asset", JSONObject().put("version", "2.0").put("generator", "3D Asset Studio overlay"))
            put("extensionsUsed", JSONArray().put("KHR_materials_unlit"))
            put("scene", 0)
            put("scenes", JSONArray().put(JSONObject().put("nodes", JSONArray().put(0))))
            put("nodes", JSONArray().put(JSONObject().put("mesh", 0).put("name", "DebugOverlay")))
            put("meshes", JSONArray().put(JSONObject().put("primitives", JSONArray().put(
                JSONObject().put("attributes", JSONObject().put("POSITION", 0).put("COLOR_0", 1))
                    .put("indices", 2).put("mode", mode).put("material", 0)))))
            put("materials", JSONArray().put(JSONObject()
                .put("pbrMetallicRoughness", JSONObject()
                    .put("baseColorFactor", JSONArray().put(1).put(1).put(1).put(1))
                    .put("metallicFactor", 0.0).put("roughnessFactor", 1.0))
                .put("doubleSided", true)
                .put("extensions", JSONObject().put("KHR_materials_unlit", JSONObject()))))
            put("accessors", JSONArray()
                .put(JSONObject().put("bufferView", 0).put("componentType", 5126)
                    .put("count", positions.size / 3).put("type", "VEC3")
                    .put("min", extrema(positions, 3, true)).put("max", extrema(positions, 3, false)))
                .put(JSONObject().put("bufferView", 1).put("componentType", 5126)
                    .put("count", colours.size / 3).put("type", "VEC3"))
                .put(JSONObject().put("bufferView", 2).put("componentType", 5123)
                    .put("count", indices.size).put("type", "SCALAR")))
            put("bufferViews", JSONArray()
                .put(JSONObject().put("buffer", 0).put("byteOffset", pOff).put("byteLength", p.size))
                .put(JSONObject().put("buffer", 0).put("byteOffset", cOff).put("byteLength", c.size))
                .put(JSONObject().put("buffer", 0).put("byteOffset", iOff).put("byteLength", idx.size)))
            put("buffers", JSONArray().put(JSONObject().put("byteLength", bin.size)))
        }
        val jb = json.toString().toByteArray(Charsets.UTF_8)
        val jp = jb.copyOf(align4(jb.size))
        for (i in jb.size until jp.size) jp[i] = 0x20
        val total = 12 + 8 + jp.size + 8 + bin.size
        return ByteBuffer.allocate(total).order(ByteOrder.LITTLE_ENDIAN).apply {
            putInt(0x46546C67); putInt(2); putInt(total)
            putInt(jp.size); putInt(0x4E4F534A); put(jp)
            putInt(bin.size); putInt(0x004E4942); put(bin)
            flip()
        }
    }

    fun build(lines: List<DebugLine>): ByteBuffer {
        val p = mutableListOf<Float>(); val c = mutableListOf<Float>(); val idx = mutableListOf<Int>()
        lines.forEach { l ->
            val base = p.size / 3
            p += l.start.toList(); p += l.end.toList()
            c += l.colour.toList(); c += l.colour.toList()
            idx += base; idx += base + 1
        }
        return buildGeometry(p, c, idx, mode = 1)
    }

    fun ground(size: Float = 5f): ByteBuffer {
        val col = listOf(0.22f, 0.26f, 0.30f)
        return buildGeometry(listOf(-size,0f,-size, size,0f,-size, size,0f,size, -size,0f,size),
            List(4) { col }.flatten(), listOf(0,1,2,0,2,3), mode = 4)
    }

    fun gridAndAxes(grid: Boolean, axes: Boolean): List<DebugLine> {
        val lines = mutableListOf<DebugLine>()
        if (grid) {
            val grey = floatArrayOf(0.28f, 0.32f, 0.38f)
            for (i in -10..10) {
                val v = i * 0.5f
                lines += DebugLine(floatArrayOf(-5f,0f,v), floatArrayOf(5f,0f,v), grey)
                lines += DebugLine(floatArrayOf(v,0f,-5f), floatArrayOf(v,0f,5f), grey)
            }
        }
        if (axes) {
            lines += DebugLine(floatArrayOf(0f,0f,0f), floatArrayOf(1f,0f,0f), floatArrayOf(1f,.25f,.25f))
            lines += DebugLine(floatArrayOf(0f,0f,0f), floatArrayOf(0f,1f,0f), floatArrayOf(.25f,1f,.25f))
            lines += DebugLine(floatArrayOf(0f,0f,0f), floatArrayOf(0f,0f,1f), floatArrayOf(.25f,.55f,1f))
        }
        return lines
    }
}
