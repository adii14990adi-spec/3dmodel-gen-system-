package com.assetstudio.app.screens

import android.view.SurfaceView
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.navigation.NavController
import com.assetstudio.app.Routes
import com.assetstudio.app.StudioViewModel
import com.assetstudio.app.components.*
import com.assetstudio.core.model.CameraPreset
import com.assetstudio.core.model.Quality
import com.assetstudio.core.model.RenderMode
import com.assetstudio.core.util.Formatting
import com.assetstudio.data.prefs.SettingsRepository
import com.assetstudio.library.download.DownloadState
import com.assetstudio.renderer.FilamentViewport
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScreenScaffold(title: String, nav: NavController, content: @Composable ColumnScope.() -> Unit) {
    Scaffold(topBar = {
        TopAppBar(title = { Text(title) }, navigationIcon = {
            IconButton(onClick = { nav.navigate(Routes.HOME) { popUpTo(Routes.HOME) { inclusive = true } } }) {
                Icon(Icons.Default.Home, contentDescription = "Home")
            }
        })
    }) { padding ->
        Column(Modifier.padding(padding).padding(14.dp).fillMaxSize(), content = content)
    }
}

@Composable
fun HomeScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val destinations = listOf(
        "Asset Library" to Routes.LIBRARY, "Model Browser" to Routes.MODELS,
        "3D Viewport" to Routes.VIEWPORT, "Skeleton Inspector" to Routes.SKELETON,
        "Animation Library" to Routes.ANIMATIONS, "Animation Player" to Routes.PLAYER,
        "Material Inspector" to Routes.MATERIALS, "Geometry Inspector" to Routes.GEOMETRY,
        "Validation Report" to Routes.VALIDATION, "Downloads" to Routes.DOWNLOADS,
        "Settings" to Routes.SETTINGS)
    ScreenScaffold("3D Asset Studio", nav) {
        Text("Native Filament viewer and inspection tool. This app renders downloaded GLB/glTF assets; it does not generate or edit 3D models.",
            style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Section("Remote assets", Modifier.weight(1f)) {
                Text("${ui.assets.size}", style = MaterialTheme.typography.headlineMedium)
                Text("models ${ui.health?.assets?.get("models") ?: 0} · animations ${ui.health?.assets?.get("animations") ?: 0}",
                    style = MaterialTheme.typography.bodySmall)
            }
            Section("Offline cache", Modifier.weight(1f)) {
                Text("${ui.cached.size}", style = MaterialTheme.typography.headlineMedium)
                Text(Formatting.bytes(ui.cacheBytes), style = MaterialTheme.typography.bodySmall)
            }
        }
        if (ui.message.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(ui.message, color = MaterialTheme.colorScheme.tertiary, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(12.dp))
        Button(onClick = vm::refresh, Modifier.fillMaxWidth()) { Text("Refresh metadata from API") }
        Spacer(Modifier.height(12.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(destinations) { (label, route) ->
                Card(Modifier.fillMaxWidth().clickable { nav.navigate(route) }) {
                    Row(Modifier.padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(label, style = MaterialTheme.typography.titleSmall)
                        Icon(Icons.Default.ArrowForward, contentDescription = null)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AssetLibraryScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val ctx = LocalContext.current
    val importLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let { vm.importLocal(it, it.lastPathSegment ?: "imported.glb") }
    }
    var query by remember { mutableStateOf("") }
    var sort by remember { mutableStateOf("name") }
    ScreenScaffold("Asset Library", nav) {
        OutlinedTextField(value = query, onValueChange = {
            query = it; vm.search(it)
        }, label = { Text("Search assets") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("name" to "Name", "recent" to "Recent", "size" to "Size").forEach { (k, label) ->
                FilterChip(selected = sort == k, onClick = { sort = k; vm.sort(k) },
                    label = { Text(label) })
            }
            Spacer(Modifier.weight(1f))
            Button(onClick = { importLauncher.launch(arrayOf("model/gltf-binary", "model/gltf+json", "*/*")) }) {
                Text("Import GLB/glTF")
            }
        }
        when (val d = ui.download) {
            is DownloadState.Progress -> LinearProgressIndicator(
                if (d.totalBytes > 0) d.bytesRead.toFloat() / d.totalBytes else 0f, Modifier.fillMaxWidth())
            is DownloadState.Failed -> Text("Download failed: ${d.message}", color = MaterialTheme.colorScheme.error)
            is DownloadState.Finished -> Text("Cached ${Formatting.bytes(d.bytes)} · SHA-256 ${d.sha256.take(12)}…",
                style = MaterialTheme.typography.bodySmall)
            null -> Unit
        }
        Spacer(Modifier.height(8.dp))
        if (ui.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(ui.assets) { a ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Row(horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                            Text(a.name, style = MaterialTheme.typography.titleSmall)
                            Text(a.validationStatus, style = MaterialTheme.typography.labelSmall)
                        }
                        Text("${a.kind} · ${a.category} · ${a.sourceFormat} · ${Formatting.bytes(a.processedBytes)}",
                            style = MaterialTheme.typography.bodySmall)
                        if (!a.processedPath.isNullOrEmpty() && a.validationStatus != "ERROR")
                            Text("Runtime GLB available", style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.primary)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { vm.download(a) }, enabled = !a.processedPath.isNullOrEmpty()) {
                                Text("Download")
                            }
                            OutlinedButton(onClick = {
                                if (a.kind == "model") { vm.selectModel(a.id); nav.navigate(Routes.VIEWPORT) }
                                else { vm.selectAnimation(a.id); nav.navigate(Routes.ANIMATIONS) }
                            }) { Text("Open") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ModelBrowserScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val models = ui.assets.filter { it.kind == "model" }
    ScreenScaffold("Model Browser", nav) {
        Text("${models.size} published models. Folder names are organisational metadata; inspect the file for real properties.",
            style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(10.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(models) { m ->
                Card(Modifier.fillMaxWidth().clickable { vm.selectModel(m.id) }) {
                    Column(Modifier.padding(12.dp)) {
                        Text(m.name, style = MaterialTheme.typography.titleSmall)
                        Text("${m.category} · ${m.sourceFormat} · humanoid: ${m.humanoid} · ${m.validationStatus}",
                            style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            OutlinedButton(onClick = { vm.selectModel(m.id); nav.navigate(Routes.GEOMETRY) }) { Text("Geometry") }
                            OutlinedButton(onClick = { vm.selectModel(m.id); nav.navigate(Routes.MATERIALS) }) { Text("Materials") }
                            OutlinedButton(onClick = { vm.selectModel(m.id); nav.navigate(Routes.SKELETON) }) { Text("Skeleton") }
                            OutlinedButton(onClick = { vm.selectModel(m.id); nav.navigate(Routes.VALIDATION) }) { Text("Report") }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ViewportScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var viewport by remember { mutableStateOf<FilamentViewport?>(null) }
    var fps by remember { mutableStateOf(0f) }
    val scope = rememberCoroutineScope()
    val settings = ui.settings
    val candidates = ui.cached.filter { it.kind == "model" }
    ScreenScaffold("3D Viewport", nav) {
        if (candidates.isEmpty()) {
            EmptyState("No cached model yet. Download a model in the Asset Library, then reopen the viewport. Local GLB/glTF imports also appear here.")
        } else {
            AndroidView(factory = { ctx ->
                SurfaceView(ctx).also { sv ->
                    val v = FilamentViewport(sv)
                    viewport = v; vm.viewport = v
                    lifecycle.addObserver(v)
                    v.setGrid(settings?.showGrid ?: true)
                    v.setAxes(settings?.showAxes ?: true)
                    v.setGround(settings?.showGround ?: false)
                    v.setSkeleton(settings?.showSkeleton ?: false)
                    v.applyQuality(settings?.quality ?: Quality.BALANCED)
                    v.applyRenderMode(settings?.renderMode ?: RenderMode.PBR)
                    v.setHdr(settings?.hdrEnabled ?: true)
                    v.setShadows(settings?.shadowsEnabled ?: true)
                    v.orthographic = settings?.orthographic ?: false
                    v.exposure = settings?.exposure ?: 1f
                    v.skeletonLines = vm.skeletonInspector.overlayLines()
                    v.onModelLoaded = { count ->
                        vm.animationController.bind(count, (0 until count).map { v.animationName(it) },
                            (0 until count).map { v.animationDuration(it) })
                    }
                    v.loadFile(candidates.first().localPath)
                    v.start()
                }
            }, modifier = Modifier.fillMaxWidth().height(280.dp))
            DisposableEffect(Unit) { onDispose { viewport?.destroy(); vm.viewport = null } }
            LaunchedEffect(Unit) { while (true) {
                kotlinx.coroutines.delay(500); fps = viewport?.fps ?: 0f
            } }
            Text("Rendering ${candidates.first().name} · FPS ${"%.1f".format(fps)}", style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(8.dp))
            Text("Touch: orbit · pinch zoom · two-finger pan. Axis views:", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
                listOf(CameraPreset.FRONT, CameraPreset.BACK, CameraPreset.LEFT,
                    CameraPreset.RIGHT, CameraPreset.TOP, CameraPreset.BOTTOM).forEach { p ->
                    OutlinedButton(onClick = { viewport?.camera(p) }, contentPadding = PaddingValues(4.dp)) {
                        Text(p.name.take(3), style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(onClick = { viewport?.resetCamera() }) { Text("Reset") }
                Button(onClick = {
                    val next = !(settings?.orthographic ?: false)
                    viewport?.orthographic = next
                    scope.launch { vm.setSetting(SettingsRepository.ORTHOGRAPHIC, next) }
                }) { Text(if (settings?.orthographic == true) "Perspective" else "Orthographic") }
            }
            Spacer(Modifier.height(8.dp))
            Text("View toggles", style = MaterialTheme.typography.titleSmall)
            val toggles = listOf(
                "Grid" to (settings?.showGrid ?: true) to SettingsRepository.SHOW_GRID,
                "Axes" to (settings?.showAxes ?: true) to SettingsRepository.SHOW_AXES,
                "Ground" to (settings?.showGround ?: false) to SettingsRepository.SHOW_GROUND,
                "Skeleton overlay" to (settings?.showSkeleton ?: false) to SettingsRepository.SHOW_SKELETON,
                "HDR" to (settings?.hdrEnabled ?: true) to SettingsRepository.HDR_ENABLED,
                "Shadows" to (settings?.shadowsEnabled ?: true) to SettingsRepository.SHADOWS_ENABLED)
            toggles.forEach { (pair, key) ->
                val (label, value) = pair
                ToggleRow(label, value) { on ->
                    scope.launch { vm.setSetting(key, on) }
                    when (key.name) {
                        "show_grid" -> viewport?.setGrid(on)
                        "show_axes" -> viewport?.setAxes(on)
                        "show_ground" -> viewport?.setGround(on)
                        "show_skeleton" -> viewport?.setSkeleton(on)
                        "hdr_enabled" -> viewport?.setHdr(on)
                        "shadows_enabled" -> viewport?.setShadows(on)
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Text("Render mode", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                RenderMode.entries.forEach { m ->
                    FilterChip(selected = settings?.renderMode == m, onClick = {
                        viewport?.applyRenderMode(m)
                        scope.launch { vm.setSetting(SettingsRepository.RENDER_MODE, m.name) }
                    }, label = { Text(m.name, style = MaterialTheme.typography.labelSmall) })
                }
            }
            Spacer(Modifier.height(8.dp))
            Text("Quality (Balanced is the default)", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Quality.entries.forEach { q ->
                    FilterChip(selected = (settings?.quality ?: Quality.BALANCED) == q, onClick = {
                        viewport?.applyQuality(q)
                        scope.launch { vm.setSetting(SettingsRepository.QUALITY, q.name) }
                    }, label = { Text(q.name.lowercase().replaceFirstChar { it.uppercase() }) })
                }
            }
            Spacer(Modifier.height(8.dp))
            Text("Exposure ${"%.2f".format(settings?.exposure ?: 1f)}", style = MaterialTheme.typography.bodySmall)
            Slider(value = settings?.exposure ?: 1f, onValueChange = { v ->
                viewport?.exposure = v
                scope.launch { vm.setSetting(SettingsRepository.EXPOSURE, v) }
            }, valueRange = 0.2f..2.5f)
        }
    }
}

@Composable
fun SkeletonInspectorScreen(vm: StudioViewModel, nav: NavController) {
    val state by vm.skeletonInspector.state.collectAsState()
    val sk = state.skeleton
    val scope = rememberCoroutineScope()
    ScreenScaffold("Skeleton Inspector", nav) {
        if (!sk.present) {
            EmptyState("Static model: no skeleton in the loaded asset. The app never fabricates bone hierarchies. Open a rigged model in the Model Browser first.")
            return@ScreenScaffold
        }
        Section("Skeleton summary") {
            KeyValues(listOf(
                com.assetstudio.inspector.GeometryInspector.Row("Bones", "${sk.boneCount}"),
                com.assetstudio.inspector.GeometryInspector.Row("Skeletons/skins", "${sk.skeletonCount} / ${sk.skins.size}"),
                com.assetstudio.inspector.GeometryInspector.Row("Root bone", sk.rootBone ?: "n/a"),
                com.assetstudio.inspector.GeometryInspector.Row("Humanoid", "${sk.humanoid} (conf ${"%.2f".format(sk.humanoidConfidence)})")))
        }
        Spacer(Modifier.height(8.dp))
        Section("Bone hierarchy (real names, parent/children)") {
            // Flatten the tree first; Composable calls must not live inside a local helper.
            val rows = mutableListOf<Pair<String, Int>>()
            fun walk(name: String, depth: Int) {
                val b = sk.bone(name) ?: return
                rows += name to depth
                if (name in state.expanded) b.children.forEach { walk(it, depth + 1) }
            }
            vm.skeletonInspector.rootBones().forEach { walk(it.name, 0) }
            rows.forEach { (name, depth) ->
                val b = sk.bone(name) ?: return@forEach
                Row(Modifier.fillMaxWidth().padding(start = (depth * 12).dp)
                    .clickable { vm.skeletonInspector.select(name) }) {
                    Text("${if (state.selectedBone?.name == name) "●" else "○"} ${b.name} " +
                        "(parent: ${b.parent ?: "root"}, children: ${b.children.size})",
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        state.selectedBone?.let { b ->
            Section("Selected bone: ${b.name}") {
                KeyValues(listOf(
                    com.assetstudio.inspector.GeometryInspector.Row("Index", "${b.index}"),
                    com.assetstudio.inspector.GeometryInspector.Row("Parent", b.parent ?: "root"),
                    com.assetstudio.inspector.GeometryInspector.Row("Children", b.children.joinToString().ifEmpty { "none" }),
                    com.assetstudio.inspector.GeometryInspector.Row("Translation", b.translation.joinToString { "%.3f".format(it) }),
                    com.assetstudio.inspector.GeometryInspector.Row("Rotation (quat)", b.rotation.joinToString { "%.3f".format(it) }),
                    com.assetstudio.inspector.GeometryInspector.Row("Scale", b.scale.joinToString { "%.3f".format(it) }),
                    com.assetstudio.inspector.GeometryInspector.Row("Rest head", "${b.head}")))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    OutlinedButton(onClick = vm.skeletonInspector::selectParent) { Text("Parent") }
                    OutlinedButton(onClick = vm.skeletonInspector::selectFirstChild) { Text("First child") }
                    OutlinedButton(onClick = { vm.skeletonInspector.toggleExpanded(b.name) }) { Text("Expand/collapse") }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Section("Skinning validation (from pipeline)") {
            vm.skeletonInspector.skinningDiagnostics().forEach { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
        Spacer(Modifier.height(8.dp))
        ToggleRow("Show skeleton overlay", state.showNames) { vm.skeletonInspector.setShowNames(it) }
        ToggleRow("Skeleton-only view", state.skeletonOnly) {
            vm.skeletonInspector.setSkeletonOnly(it)
            vm.viewport?.setSkeleton(it)
            scope.launch { vm.setSetting(SettingsRepository.SKELETON_ONLY, it) }
        }
    }
}

@Composable
fun AnimationLibraryScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val animations = ui.assets.filter { it.kind == "animation" }
    ScreenScaffold("Animation Library", nav) {
        Text("${animations.size} published animations. Only validated, exported GLBs are playable.",
            style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(8.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(animations) { a ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(a.name, style = MaterialTheme.typography.titleSmall)
                        Text("${a.category} · ${a.sourceFormat} · retarget: ${a.retargetStatus.ifEmpty { "—" }} · target: ${a.targetCharacterId.ifBlank { "—" }}",
                            style = MaterialTheme.typography.bodySmall)
                        Text("Status ${a.validationStatus}", style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Button(onClick = { vm.download(a) }, enabled = !a.processedPath.isNullOrEmpty()) { Text("Download") }
                            OutlinedButton(onClick = { vm.selectAnimation(a.id); nav.navigate(Routes.PLAYER) }) {
                                Text("Player")
                            }
                        }
                    }
                }
            }
        }
    }
}
