package com.assetstudio.core.model

import com.assetstudio.core.util.Matrix4

/**
 * Domain models shared across modules. These are populated ONLY from metadata
 * produced by the processing pipeline (which inspects real GLB bytes).
 * No skeleton, material or animation data is ever invented by the app.
 */
data class BoundingBox(val min: List<Float>, val max: List<Float>)

data class GeometryStats(
    val meshCount: Int = 0, val primitiveCount: Int = 0, val vertexCount: Int = 0,
    val triangleCount: Int = 0, val indexCount: Int = 0, val materialCount: Int = 0,
    val textureCount: Int = 0, val uvSets: List<String> = emptyList(),
    val hasNormals: Boolean = false, val hasTangents: Boolean = false,
    val morphTargetCount: Int = 0, val boundingBox: BoundingBox? = null,
    val dimensions: List<Float> = emptyList()
)

data class TextureSlot(val texture: Int, val texCoord: Int = 0, val scale: Float = 1f, val strength: Float = 1f)

data class MaterialInfo(
    val name: String, val index: Int,
    val baseColorFactor: List<Float> = listOf(1f, 1f, 1f, 1f),
    val metallicFactor: Float = 1f, val roughnessFactor: Float = 1f,
    val emissiveFactor: List<Float> = listOf(0f, 0f, 0f),
    val alphaMode: String = "OPAQUE", val alphaCutoff: Float = 0.5f,
    val doubleSided: Boolean = false, val textures: Map<String, TextureSlot> = emptyMap(),
    val missingTextures: List<String> = emptyList()
)

data class TextureInfo(
    val name: String, val index: Int, val uri: String? = null, val mimeType: String? = null,
    val width: Int? = null, val height: Int? = null, val bytes: Long? = null,
    val present: Boolean = true
)

data class Bone(
    val name: String, val index: Int, val parent: String?, val children: List<String> = emptyList(),
    val translation: List<Float> = listOf(0f, 0f, 0f),
    val rotation: List<Float> = listOf(0f, 0f, 0f, 1f),
    val scale: List<Float> = listOf(1f, 1f, 1f),
    val globalMatrix: List<Float> = Matrix4.IDENTITY, val length: Float = 0f
) { val head: Triple<Float, Float, Float> get() = Triple(globalMatrix[12], globalMatrix[13], globalMatrix[14]) }

data class SkinInfo(
    val name: String, val jointCount: Int, val rootBone: String?, val skeletonNode: String?,
    val joints: List<String>, val hasInverseBindMatrices: Boolean,
    val skinnedMeshes: List<String> = emptyList(),
    val vertexInfluenceCounts: Map<String, Int> = emptyMap(),
    val unweightedVertices: Int = 0, val invalidWeightVertices: Int = 0,
    val missingJoints: List<String> = emptyList(),
    val hasJointsAttribute: Boolean = false, val hasWeightsAttribute: Boolean = false
)

data class Skeleton(
    val present: Boolean, val skeletonCount: Int = 0, val rootBone: String? = null,
    val boneCount: Int = 0, val bones: List<Bone> = emptyList(),
    val skins: List<SkinInfo> = emptyList(), val humanoid: Boolean = false,
    val humanoidConfidence: Float = 0f
) { fun bone(name: String): Bone? = bones.firstOrNull { it.name == name } }

data class AnimationClip(
    val name: String, val index: Int, val durationSeconds: Float = 0f,
    val frameRate: Float? = null, val frameStart: Float = 0f, val frameEnd: Float = 0f,
    val channelCount: Int = 0, val animatedBones: List<String> = emptyList(),
    val rootMotion: Boolean = false, val interpolation: String = "LINEAR"
)

data class CompatibilityRecord(
    val targetCharacterId: String, val targetName: String? = null, val status: String,
    val score: Float = 0f, val roleCoverage: Float = 0f, val mappedBones: Int = 0,
    val unmappedRoles: List<String> = emptyList(), val ambiguousRoles: List<String> = emptyList(),
    val reasons: List<String> = emptyList()
)

data class ValidationCheck(
    val severity: String, val code: String, val description: String,
    val asset: String, val location: String, val suggestedFix: String
)

data class ValidationInfo(
    val status: String = "UNKNOWN", val pass: Int = 0, val warning: Int = 0,
    val error: Int = 0, val report: String = "", val checks: List<ValidationCheck> = emptyList()
)

data class AssetSummary(
    val id: String, val kind: String, val name: String, val category: String = "",
    val sourcePath: String = "", val sourceFormat: String = "", val sourceSha256: String = "",
    val processedPath: String? = null, val processedBytes: Long = 0,
    val processedSha256: String = "", val validationStatus: String = "",
    val humanoid: Boolean = false, val retargetStatus: String = "",
    val targetCharacterId: String = "", val updatedAt: String = ""
)

data class ModelDetail(
    val summary: AssetSummary, val geometry: GeometryStats = GeometryStats(),
    val materials: List<MaterialInfo> = emptyList(), val textures: List<TextureInfo> = emptyList(),
    val skeleton: Skeleton = Skeleton(false), val clips: List<AnimationClip> = emptyList(),
    val validation: ValidationInfo = ValidationInfo()
)

data class AnimationDetail(
    val summary: AssetSummary, val clips: List<AnimationClip> = emptyList(),
    val frameRate: Float? = null, val rootMotion: Boolean = false,
    val sourceSkeleton: Skeleton = Skeleton(false),
    val compatibility: List<CompatibilityRecord> = emptyList(),
    val playable: Boolean = false, val incompatibilityReason: String = "",
    val validation: ValidationInfo = ValidationInfo()
)

data class CompatibleAnimation(
    val id: String, val name: String, val category: String = "",
    val durationSeconds: Float = 0f, val frameRate: Float? = null,
    val rootMotion: Boolean = false, val retargetStatus: String = "",
    val targetCharacterId: String = "", val compatible: Boolean = false,
    val playable: Boolean = false, val reason: String = "", val downloadReference: String = ""
)

enum class Quality { PERFORMANCE, BALANCED, HIGH }
enum class RenderMode { PBR, TEXTURED, SOLID, WIREFRAME, XRAY, NORMALS }
enum class CameraPreset { FRONT, BACK, LEFT, RIGHT, TOP, BOTTOM, RESET }
