package com.assetstudio.renderer

import android.annotation.SuppressLint
import android.view.Choreographer
import android.view.MotionEvent
import android.view.SurfaceView
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.assetstudio.core.model.CameraPreset
import com.assetstudio.core.model.Quality
import com.assetstudio.core.model.RenderMode
import com.google.android.filament.Camera
import com.google.android.filament.EntityManager
import com.google.android.filament.gltfio.AssetLoader
import com.google.android.filament.gltfio.FilamentAsset
import com.google.android.filament.gltfio.UbershaderProvider
import com.google.android.filament.utils.ModelViewer
import java.io.File
import java.nio.ByteBuffer

/** Native Google Filament viewport: real GLB/glTF rendering + Choreographer loop. */
class FilamentViewport(private val surfaceView: SurfaceView) :
    Choreographer.FrameCallback, DefaultLifecycleObserver {

    private val choreographer = Choreographer.getInstance()
    private val modelViewer = ModelViewer(surfaceView)
    private val engine = modelViewer.engine
    private val scene = modelViewer.scene
    private val materialProvider = UbershaderProvider(engine)
    private val overlayLoader = AssetLoader(engine, materialProvider, EntityManager.get())
    private var overlayAsset: FilamentAsset? = null
    private var groundAsset: FilamentAsset? = null

    private var running = false
    private var lastFrame = 0L
    private var lastTick = 0L
    private var frameInterval = 0L
    private var frames = 0
    private var fpsStart = 0L
    private var _fps = 0f
    val fps: Float get() = _fps

    var quality: Quality = Quality.BALANCED
        set(v) { field = v; frameInterval = if (v == Quality.PERFORMANCE) 33_000_000L else 0L }
    var renderMode: RenderMode = RenderMode.PBR
    var showGrid = true
    var showAxes = true
    var showGround = false
    var showSkeleton = false
    var skeletonLines: List<DebugLine> = emptyList()
        set(v) { field = v; refreshOverlay() }
    var exposure = 1f
        set(v) { field = v; invokeOptional(modelViewer.camera, "setExposure", arrayOf(v)) }
    var orthographic = false
        set(v) { field = v; applyProjection() }
    var onModelLoaded: ((Int) -> Unit)? = null
    var animationTick: ((Long) -> Unit)? = null

    init {
        @SuppressLint("ClickableViewAccessibility")
        surfaceView.setOnTouchListener { _, e ->
            modelViewer.onTouchEvent(e); true
        }
        refreshOverlay()
        applyProjection()
    }

    fun loadGlb(bytes: ByteBuffer) {
        destroyModel()
        modelViewer.loadModelGlb(bytes)
        modelViewer.transformToUnitCube()
        resetCamera()
        onModelLoaded?.invoke(animationCount())
    }

    fun loadFile(path: String) = loadGlb(ByteBuffer.wrap(File(path).readBytes()))

    fun animationCount(): Int = runCatching { modelViewer.animator?.getAnimationCount() ?: 0 }.getOrDefault(0)
    fun animationDuration(i: Int): Float = runCatching { modelViewer.animator?.getAnimationDuration(i) ?: 0f }.getOrDefault(0f)
    fun animationName(i: Int): String = runCatching { modelViewer.animator?.getAnimationName(i) ?: "clip_$i" }.getOrDefault("clip_$i")

    fun applyAnimation(index: Int, seconds: Float) {
        val animator = modelViewer.animator ?: return
        val count = runCatching { animator.getAnimationCount() }.getOrDefault(0)
        if (index !in 0 until count) return
        runCatching { animator.applyAnimation(index, seconds); animator.updateBoneMatrices() }
    }

    fun applyRenderMode(mode: RenderMode) {
        renderMode = mode
        val asset = modelViewer.asset ?: return
        runCatching {
            for (i in 0 until asset.materialInstanceCount) {
                val mi = asset.getMaterialInstanceAt(i)
                when (mode) {
                    RenderMode.SOLID -> mi.setParameter("baseColorFactor", 0.75f, 0.78f, 0.82f, 1f)
                    RenderMode.WIREFRAME, RenderMode.XRAY, RenderMode.NORMALS ->
                        mi.setParameter("baseColorFactor", 0.35f, 0.85f, 0.80f,
                            if (mode == RenderMode.XRAY) 0.35f else 1f)
                    RenderMode.PBR, RenderMode.TEXTURED -> Unit // original glTF PBR materials
                }
            }
        }
    }

    fun applyQuality(q: Quality) {
        quality = q
        invokeOptional(modelViewer.view, "setSampleCount", arrayOf(if (q == Quality.HIGH) 4 else 1))
    }

    fun setHdr(on: Boolean) = invokeOptional(modelViewer.view, "setPostProcessingEnabled", arrayOf(on))
    fun setShadows(on: Boolean) = invokeOptional(modelViewer.view, "setShadowingEnabled", arrayOf(on))
    fun setGrid(on: Boolean) { showGrid = on; refreshOverlay() }
    fun setAxes(on: Boolean) { showAxes = on; refreshOverlay() }
    fun setGround(on: Boolean) { showGround = on; refreshOverlay() }
    fun setSkeleton(on: Boolean) { showSkeleton = on; refreshOverlay() }

    fun resetCamera() = camera(CameraPreset.RESET)

    fun camera(preset: CameraPreset) {
        val (ex,ey,ez,ux,uy,uz) = when (preset) {
            CameraPreset.FRONT -> floatArrayOf(0f,0f,4f,0f,1f,0f)
            CameraPreset.BACK -> floatArrayOf(0f,0f,-4f,0f,1f,0f)
            CameraPreset.LEFT -> floatArrayOf(-4f,0f,0f,0f,1f,0f)
            CameraPreset.RIGHT -> floatArrayOf(4f,0f,0f,0f,1f,0f)
            CameraPreset.TOP -> floatArrayOf(0f,4f,0.001f,0f,0f,-1f)
            CameraPreset.BOTTOM -> floatArrayOf(0f,-4f,0.001f,0f,0f,1f)
            CameraPreset.RESET -> floatArrayOf(2.4f,1.6f,2.8f,0f,1f,0f)
        }
        runCatching { modelViewer.camera.lookAt(ex.toDouble(),ey.toDouble(),ez.toDouble(),
            0.0,0.9,0.0,ux.toDouble(),uy.toDouble(),uz.toDouble()) }
        applyProjection()
    }

    private fun applyProjection() {
        runCatching { modelViewer.camera.setProjection(45.0,
            surfaceView.width.toDouble() / surfaceView.height.coerceAtLeast(1).toDouble(),
            0.05, 100.0, Camera.Fov.VERTICAL) }
        if (orthographic) invokeOptional(modelViewer.camera, "setProjection",
            arrayOf(Camera.Projection.ORTHO, -1.5, 1.5, -1.5, 1.5, 0.05, 100.0))
    }

    /** Applies optional Filament APIs without hard compile-time coupling to version specifics. */
    private fun invokeOptional(target: Any, method: String, args: Array<Any>) {
        runCatching {
            val types = args.map { it.javaClass }.toTypedArray()
            val found = target.javaClass.methods.firstOrNull { m ->
                m.name == method && m.parameterTypes.size == types.size &&
                    m.parameterTypes.indices.all { i ->
                        m.parameterTypes[i].isAssignableFrom(types[i]) ||
                        (m.parameterTypes[i].isPrimitive &&
                            m.parameterTypes[i].simpleName.equals(types[i].simpleName, true)) }
            }
            found?.invoke(target, *args)
        }
    }

    fun refreshOverlay() {
        runCatching {
            overlayAsset?.let { scene.removeEntity(it.root) }; overlayAsset = null
            val lines = DebugGlbBuilder.gridAndAxes(showGrid, showAxes).toMutableList()
            if (showSkeleton) lines += skeletonLines
            if (lines.isNotEmpty()) {
                val a = overlayLoader.createAssetFromBinary(DebugGlbBuilder.build(lines))
                if (a != null) { scene.addEntity(a.root); overlayAsset = a }
            }
        }
        runCatching {
            groundAsset?.let { scene.removeEntity(it.root) }; groundAsset = null
            if (showGround) {
                val g = overlayLoader.createAssetFromBinary(DebugGlbBuilder.ground())
                if (g != null) { scene.addEntity(g.root); groundAsset = g }
            }
        }
    }

    override fun doFrame(nanos: Long) {
        if (!running) return
        choreographer.postFrameCallback(this)
        if (frameInterval > 0 && nanos - lastFrame < frameInterval) return
        if (lastTick > 0L) animationTick?.invoke(nanos - lastTick)
        lastTick = nanos
        lastFrame = nanos
        modelViewer.render(nanos)
        frames++
        if (fpsStart == 0L) fpsStart = nanos
        if (nanos - fpsStart >= 1_000_000_000L) {
            _fps = frames * 1_000_000_000f / (nanos - fpsStart)
            frames = 0; fpsStart = nanos
        }
    }

    override fun onResume(owner: LifecycleOwner) { start() }
    override fun onPause(owner: LifecycleOwner) { stop() }
    override fun onDestroy(owner: LifecycleOwner) { destroy() }
    fun start() { if (running) return; running = true; lastTick = 0L; choreographer.postFrameCallback(this) }
    fun stop() { running = false; choreographer.removeFrameCallback(this) }
    fun destroyModel() { runCatching { modelViewer.destroyModel() } }
    fun destroy() {
        stop(); destroyModel()
        runCatching {
            overlayAsset?.let { scene.removeEntity(it.root) }
            groundAsset?.let { scene.removeEntity(it.root) }
            overlayLoader.destroy(); materialProvider.destroyMaterials()
            materialProvider.destroy(); engine.destroy()
        }
    }
}
