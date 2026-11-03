package com.assetstudio.core.util

import com.assetstudio.core.model.*
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.sqrt

object Matrix4 {
    val IDENTITY: List<Float> = listOf(1f,0f,0f,0f, 0f,1f,0f,0f, 0f,0f,1f,0f, 0f,0f,0f,1f)
    fun translation(m: List<Float>): FloatArray =
        floatArrayOf(m.getOrElse(12) { 0f }, m.getOrElse(13) { 0f }, m.getOrElse(14) { 0f })
    fun distance(a: FloatArray, b: FloatArray): Float {
        val dx=a[0]-b[0]; val dy=a[1]-b[1]; val dz=a[2]-b[2]
        return sqrt(dx*dx+dy*dy+dz*dz)
    }
}

object Formatting {
    fun bytes(v: Long): String {
        if (v <= 0) return "0 B"
        val u = arrayOf("B","KiB","MiB","GiB"); var x=v.toDouble(); var i=0
        while (x >= 1024 && i < u.lastIndex) { x/=1024; i++ }
        return "%.1f %s".format(x,u[i])
    }
    fun number(v: Int) = "%,d".format(v)
    fun seconds(v: Float) = "%.2f s".format(abs(v))
    fun timeStamp(v: Float): String {
        val t=(abs(v)*1000).toInt()
        return "%02d:%02d.%02d".format(t/60000,(t/1000)%60,(t%1000)/10)
    }
}

/** Parses REAL metadata JSON; absent fields become honest empty defaults. */
object MetadataParser {
    private fun JSONObject.strings(k: String): List<String> =
        optJSONArray(k)?.let { a -> (0 until a.length()).map { a.optString(it) } } ?: emptyList()
    private fun JSONObject.floats(k: String): List<Float> =
        optJSONArray(k)?.let { a -> (0 until a.length()).map { a.optDouble(it).toFloat() } } ?: emptyList()

    fun geometry(j: JSONObject?): GeometryStats {
        if (j == null) return GeometryStats()
        val bb = j.optJSONObject("bounding_box")
        return GeometryStats(
            meshCount=j.optInt("mesh_count"), primitiveCount=j.optInt("primitive_count"),
            vertexCount=j.optInt("vertex_count"), triangleCount=j.optInt("triangle_count"),
            indexCount=j.optInt("index_count"), materialCount=j.optInt("material_count"),
            textureCount=j.optInt("texture_count"), uvSets=j.strings("uv_sets"),
            hasNormals=j.optBoolean("has_normals"), hasTangents=j.optBoolean("has_tangents"),
            morphTargetCount=j.optInt("morph_target_count"),
            boundingBox=bb?.let { BoundingBox(it.floats("min"), it.floats("max")) },
            dimensions=j.floats("dimensions"))
    }

    fun materials(a: JSONArray?): List<MaterialInfo> = (0 until (a?.length() ?: 0)).map { i ->
        val m = a!!.optJSONObject(i) ?: JSONObject()
        val slots = m.optJSONObject("textures")?.let { t ->
            t.keys().asSequence().mapNotNull { key -> t.optJSONObject(key)?.let { s ->
                key to TextureSlot(s.optInt("texture"), s.optInt("tex_coord"),
                    s.optDouble("scale",1.0).toFloat(), s.optDouble("strength",1.0).toFloat())
            } }.toMap()
        } ?: emptyMap()
        val missing = m.strings("missing_textures")
        MaterialInfo(m.optString("name","material_$i"), m.optInt("index",i),
            m.floats("base_color_factor").ifEmpty { listOf(1f,1f,1f,1f) },
            m.optDouble("metallic_factor",1.0).toFloat(), m.optDouble("roughness_factor",1.0).toFloat(),
            m.floats("emissive_factor").ifEmpty { listOf(0f,0f,0f) },
            m.optString("alpha_mode","OPAQUE"), m.optDouble("alpha_cutoff",0.5).toFloat(),
            m.optBoolean("double_sided"), slots, missing)
    }

    fun textures(a: JSONArray?): List<TextureInfo> = (0 until (a?.length() ?: 0)).map { i ->
        val t = a!!.optJSONObject(i) ?: JSONObject()
        TextureInfo(t.optString("name","texture_$i"), t.optInt("index",i),
            t.optString("uri").takeIf { it.isNotEmpty() }, t.optString("mime_type").takeIf { it.isNotEmpty() },
            t.optInt("width").takeIf { it > 0 }, t.optInt("height").takeIf { it > 0 },
            t.optLong("bytes").takeIf { it > 0 }, t.optBoolean("present",true))
    }

    fun skeleton(j: JSONObject?): Skeleton {
        if (j == null) return Skeleton(false)
        val ba = j.optJSONArray("bones")
        val bones = (0 until (ba?.length() ?: 0)).mapNotNull { i ->
            val b = ba!!.optJSONObject(i) ?: return@mapNotNull null
            Bone(b.optString("name"), b.optInt("index",i),
                b.optString("parent").takeIf { it.isNotEmpty() && it != "null" },
                b.strings("children"), b.floats("translation"), b.floats("rotation"),
                b.floats("scale"), b.floats("global_matrix").ifEmpty { Matrix4.IDENTITY },
                b.optDouble("length",0.0).toFloat())
        }
        val sa = j.optJSONArray("skins")
        val skins = (0 until (sa?.length() ?: 0)).map { i ->
            val s = sa!!.optJSONObject(i) ?: JSONObject()
            val counts = s.optJSONObject("vertex_influence_counts")?.let { o ->
                o.keys().asSequence().map { k -> k to o.optInt(k) }.toMap() } ?: emptyMap()
            SkinInfo(s.optString("name","skin_$i"), s.optInt("joint_count"),
                s.optString("root_bone").takeIf { it.isNotEmpty() },
                s.optString("skeleton_node").takeIf { it.isNotEmpty() },
                s.strings("joints"), s.optBoolean("has_inverse_bind_matrices"),
                s.strings("skinned_meshes"), counts, s.optInt("unweighted_vertices"),
                s.optInt("invalid_weight_vertices"), s.strings("missing_joints"),
                s.optBoolean("has_joints_attribute"), s.optBoolean("has_weights_attribute"))
        }
        return Skeleton(j.optBoolean("present"), j.optInt("skeleton_count"),
            j.optString("root_bone").takeIf { it.isNotEmpty() }, j.optInt("bone_count",bones.size),
            bones, skins, j.optBoolean("humanoid"),
            j.optDouble("humanoid_confidence",0.0).toFloat())
    }

    fun clips(a: JSONArray?): List<AnimationClip> = (0 until (a?.length() ?: 0)).map { i ->
        val c = a!!.optJSONObject(i) ?: JSONObject()
        AnimationClip(c.optString("name","clip_$i"), c.optInt("index",i),
            c.optDouble("duration_seconds",0.0).toFloat(),
            c.optDouble("frame_rate").toFloat().takeIf { it > 0 && !it.isNaN() },
            c.optDouble("frame_start",0.0).toFloat(), c.optDouble("frame_end",0.0).toFloat(),
            c.optInt("channel_count"), c.strings("animated_bones").ifEmpty { c.strings("animated_nodes") },
            c.optBoolean("root_motion"), c.optString("interpolation","LINEAR"))
    }

    fun validation(m: JSONObject, issues: JSONArray?): ValidationInfo {
        val v = m.optJSONObject("validation") ?: JSONObject()
        val s = v.optJSONObject("summary") ?: JSONObject()
        val checks = (0 until (issues?.length() ?: 0)).map { i ->
            val c = issues!!.optJSONObject(i) ?: JSONObject()
            ValidationCheck(c.optString("severity","WARNING"), c.optString("code"),
                c.optString("description"), c.optString("asset"), c.optString("location"),
                c.optString("suggested_fix"))
        }
        return ValidationInfo(v.optString("status","UNKNOWN"), s.optInt("PASS"),
            s.optInt("WARNING"), s.optInt("ERROR"), v.optString("report"), checks)
    }
}
