package com.assetstudio.skeleton

import com.assetstudio.core.model.Bone
import com.assetstudio.core.model.Skeleton
import com.assetstudio.core.util.Matrix4
import com.assetstudio.renderer.DebugLine
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Interactive state over the loaded model's REAL skeleton metadata only. */
class SkeletonInspector {
    data class State(
        val skeleton: Skeleton = Skeleton(false), val selectedBone: Bone? = null,
        val showNames: Boolean = true, val skeletonOnly: Boolean = false,
        val expanded: Set<String> = emptySet()
    )
    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    fun load(s: Skeleton) { _state.value = State(s,
        s.rootBone?.let { s.bone(it) }, expanded = setOfNotNull(s.rootBone)) }
    fun select(name: String?) {
        val c = _state.value
        _state.value = c.copy(selectedBone = name?.let { c.skeleton.bone(it) },
            expanded = if (name == null) c.expanded else c.expanded + name)
    }
    fun selectParent() = select(_state.value.selectedBone?.parent)
    fun selectFirstChild() = select(_state.value.selectedBone?.children?.firstOrNull())
    fun setShowNames(v: Boolean) { _state.value = _state.value.copy(showNames = v) }
    fun setSkeletonOnly(v: Boolean) { _state.value = _state.value.copy(skeletonOnly = v) }
    fun toggleExpanded(name: String) {
        val c = _state.value
        _state.value = c.copy(expanded = if (name in c.expanded) c.expanded - name else c.expanded + name)
    }
    fun rootBones(): List<Bone> = _state.value.skeleton.bones.filter { it.parent == null }
    fun childrenOf(name: String): List<Bone> = _state.value.skeleton.bones.filter { it.parent == name }

    /** Real 3D line segments between joint heads from the published rest matrices. */
    fun overlayLines(): List<DebugLine> {
        val s = _state.value
        val sk = s.skeleton
        if (!sk.present || sk.bones.isEmpty()) return emptyList()
        val byName = sk.bones.associateBy { it.name }
        return sk.bones.flatMap { bone ->
            val head = Matrix4.translation(bone.globalMatrix)
            bone.children.mapNotNull { childName ->
                val child = byName[childName] ?: return@mapNotNull null
                val highlight = s.selectedBone?.name in listOf(bone.name, child.name)
                DebugLine(head, Matrix4.translation(child.globalMatrix),
                    if (highlight) floatArrayOf(1f,.85f,.2f) else floatArrayOf(.35f,.9f,.8f))
            }
        }
    }

    fun skinningDiagnostics(): List<String> {
        val sk = _state.value.skeleton
        if (!sk.present) return listOf("Static model: no skeleton or skinning data.")
        return buildList {
            add("Skeletons: ${sk.skeletonCount}, bones: ${sk.boneCount}, humanoid: ${sk.humanoid} " +
                "(confidence ${"%.2f".format(sk.humanoidConfidence)})")
            sk.skins.forEach { s ->
                add("${s.name}: ${s.jointCount} joints, IBMS=${s.hasInverseBindMatrices}, " +
                    "JOINTS_0=${s.hasJointsAttribute}, WEIGHTS_0=${s.hasWeightsAttribute}")
                add("Unweighted vertices: ${s.unweightedVertices}; invalid weights: ${s.invalidWeightVertices}")
                if (s.vertexInfluenceCounts.isNotEmpty()) add("Vertex influences: " +
                    s.vertexInfluenceCounts.entries.sortedBy { it.key }.joinToString { "${it.key}→${it.value}" })
                if (s.missingJoints.isNotEmpty()) add("Missing joint indices: ${s.missingJoints.joinToString()}")
                add("Skinned meshes: ${s.skinnedMeshes.joinToString().ifEmpty { "none" }}")
            }
        }
    }
}
