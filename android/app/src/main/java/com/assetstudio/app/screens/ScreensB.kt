package com.assetstudio.app.screens

import android.content.Intent
import android.view.SurfaceView
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.navigation.NavController
import androidx.compose.ui.graphics.Color
import com.assetstudio.animation.PLAYBACK_SPEEDS
import com.assetstudio.animation.PlaybackCategory
import com.assetstudio.animation.ProbeStatus
import com.assetstudio.app.Routes
import com.assetstudio.app.StudioViewModel
import com.assetstudio.app.components.*
import com.assetstudio.core.model.RenderMode
import com.assetstudio.core.model.Quality
import com.assetstudio.core.util.Formatting
import com.assetstudio.data.prefs.SettingsRepository
import com.assetstudio.inspector.GeometryInspector
import com.assetstudio.inspector.MaterialInspector
import com.assetstudio.inspector.ValidationInspector
import com.assetstudio.library.download.DownloadState
import com.assetstudio.renderer.FilamentViewport
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AnimationPlayerScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val play by vm.animationController.state.collectAsState()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var viewport by remember { mutableStateOf<FilamentViewport?>(null) }
    val models = ui.assets.filter { it.kind == "model" && it.humanoid }
    var modelId by remember { mutableStateOf("") }
    var loadedLabel by remember { mutableStateOf("") }
    val reportState by vm.playbackSession.report.collectAsState()
    val validated by vm.playbackSession.validated.collectAsState()
    LaunchedEffect(models) { if (modelId.isBlank() && models.isNotEmpty()) {
        modelId = models.first().id; vm.selectModel(modelId)
    } }
    ScreenScaffold("Animation Player", nav) {
        Text("Select a rigged character. Compatibility alone does not make a clip playable — a validated exported GLB for that exact target is required.",
            style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            models.forEach { m ->
                FilterChip(selected = modelId == m.id, onClick = {
                    modelId = m.id; vm.selectModel(m.id)
                }, label = { Text(m.name, style = MaterialTheme.typography.labelSmall) })
            }
        }
        Spacer(Modifier.height(8.dp))
        val playable = ui.compatible.filter { it.playable }
        val incompatible = ui.compatible.filterNot { it.playable }
        Section("Playable retargeted clips (${playable.size})") {
            if (playable.isEmpty()) EmptyState("No playable clips for this character. Export a retargeted GLB for this exact target, or choose another model.")
            playable.forEach { a ->
                Card(Modifier.fillMaxWidth().padding(vertical = 3.dp).clickable {
                    val cached = ui.cached.firstOrNull { it.id == a.id }
                    if (cached != null) {
                        // Clip count/names/durations come from the loaded GLB's animator.
                        vm.loadPlaybackAsset(cached.localPath, a.name)
                        loadedLabel = a.name
                    } else {
                        ui.assets.firstOrNull { it.id == a.id }?.let { vm.download(it) }
                    }
                }) {
                    Column(Modifier.padding(10.dp)) {
                        Text(a.name, style = MaterialTheme.typography.titleSmall)
                        Text("${a.retargetStatus} · ${a.targetCharacterId} · ${Formatting.seconds(a.durationSeconds)}",
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        AndroidView(factory = { ctx ->
            SurfaceView(ctx).also { sv ->
                val v = FilamentViewport(sv)
                viewport = v; vm.viewport = v; lifecycle.addObserver(v)
                v.setGrid(false); v.setAxes(false); v.start()
                v.animationTick = { delta -> vm.animationController.tick(delta) }
            }
        }, modifier = Modifier.fillMaxWidth().height(220.dp))
        DisposableEffect(Unit) { onDispose { viewport?.destroy(); vm.viewport = null } }
        Spacer(Modifier.height(8.dp))
        if (!play.enabled) {
            EmptyState("No animation clips available")
        }
        Section("Playback") {
            Text("${Formatting.timeStamp(play.currentTime)} / ${Formatting.timeStamp(play.duration)}",
                style = MaterialTheme.typography.titleSmall)
            Slider(value = play.currentTime, onValueChange = vm.animationController::seek,
                valueRange = 0f..(play.duration.coerceAtLeast(0.001f)), enabled = play.enabled)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Button(onClick = vm.animationController::play, enabled = play.enabled) { Text("Play") }
                Button(onClick = vm.animationController::pause, enabled = play.enabled) { Text("Pause") }
                Button(onClick = vm.animationController::stop, enabled = play.enabled) { Text("Stop") }
                Button(onClick = vm.animationController::restart, enabled = play.enabled) { Text("Restart") }
            }
            ToggleRow("Loop", play.looping, vm.animationController::setLooping)
            Text("Speed", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                PLAYBACK_SPEEDS.forEach { s ->
                    FilterChip(selected = play.speed == s, onClick = { vm.animationController.setSpeed(s) },
                        enabled = play.enabled, label = { Text("${s}x", style = MaterialTheme.typography.labelSmall) })
                }
            }
            if (play.clipCount > 1) {
                Text("Clip", style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                    play.clipNames.forEachIndexed { i, name ->
                        FilterChip(selected = play.clipIndex == i, onClick = {
                            vm.animationController.selectClip(i, viewport?.animationDuration(i) ?: 0f)
                        }, label = { Text(name, style = MaterialTheme.typography.labelSmall) })
                    }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Section("Runtime playback validation") {
            Text("Poses the loaded clip at 0%, 50% and 99% through the Filament animator " +
                "and checks that the skeleton really moved. A PASS proves the control path and bone " +
                "matrices ran; it is not a claim about the rendered pixels.",
                style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(onClick = { vm.validatePlayback() }, enabled = play.clipCount > 0) {
                    Text("Validate loaded clip")
                }
                OutlinedButton(onClick = { loadedLabel = ""; vm.playbackSession.detach() },
                    enabled = loadedLabel.isNotBlank()) { Text("Unload") }
            }
            val report = reportState
            if (report != null) {
                Spacer(Modifier.height(6.dp))
                Text("${report.assetName}: ${report.clipCount} clip(s), ${report.frames} pose frame(s), " +
                    "${report.passed} passed / ${report.skipped} skipped / ${report.failed} failed",
                    style = MaterialTheme.typography.titleSmall)
                Text(report.note, style = MaterialTheme.typography.bodySmall)
                report.categories.filter { it.probe != null }.forEach { outcome ->
                    val probe = outcome.probe!!
                    val color = when (probe.status) {
                        ProbeStatus.PASS -> Color(0xFF2E7D32)
                        ProbeStatus.FAIL -> MaterialTheme.colorScheme.error
                        ProbeStatus.SKIP -> MaterialTheme.colorScheme.onSurfaceVariant
                    }
                    Text("${probe.status} · ${probe.category.label} · ${probe.summary}",
                        color = color, style = MaterialTheme.typography.bodySmall)
                }
            }
            Spacer(Modifier.height(6.dp))
            Text("Categories validated on this device this session: " +
                validated.keys.joinToString { it.label }.ifBlank { "none yet" },
                style = MaterialTheme.typography.bodySmall)
            PlaybackCategory.entries.chunked(3).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    row.forEach { category ->
                        val done = validated.containsKey(category)
                        FilterChip(selected = done, enabled = false,
                            onClick = {}, label = {
                                Text("${if (done) "✓" else "·"} ${category.label}",
                                    style = MaterialTheme.typography.labelSmall)
                            })
                    }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Section("Not playable (${incompatible.size})") {
            if (incompatible.isEmpty()) Text("All checked animations are playable for this character.",
                style = MaterialTheme.typography.bodySmall)
            incompatible.take(8).forEach { a ->
                Text("${a.name}: ${a.reason}", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MaterialInspectorScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val detail = ui.selectedModel
    var expanded by remember { mutableStateOf(0) }
    ScreenScaffold("Material Inspector", nav) {
        ModelPicker(vm, ui.assets.filter { it.kind == "model" })
        if (detail == null) { EmptyState("Open a model to inspect its real materials."); return@ScreenScaffold }
        val state = MaterialInspector.build(detail.materials, detail.textures)
        if (state.warnings.isNotEmpty()) {
            Section("Missing texture warnings") {
                state.warnings.forEach { Text("⚠ $it", color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall) }
            }
            Spacer(Modifier.height(8.dp))
        }
        state.materials.forEachIndexed { i, m ->
            Section("Material #$i — ${m.name}", Modifier.clickable { expanded = i }) {
                if (expanded == i) {
                    KeyValues(MaterialInspector.rows(m))
                    m.textures.forEach { (slot, ref) ->
                        state.textures.firstOrNull { it.index == ref.texture }?.let { t ->
                            Text("${slot}: ${t.name} · ${t.mimeType ?: "?"} · ${t.width ?: "?"}x${t.height ?: "?"} · ${Formatting.bytes(t.bytes ?: 0)}",
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                } else Text("Tap to expand base colour, metallic, roughness, alpha and texture slots.",
                    style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(6.dp))
        }
        Section("Texture inventory (${state.textures.size})") {
            if (state.textures.isEmpty()) Text("No textures in this asset.", style = MaterialTheme.typography.bodySmall)
            state.textures.forEach { t ->
                Text("${t.name}: ${t.mimeType ?: "?"} ${t.width ?: "?"}x${t.height ?: "?"} " +
                    "${Formatting.bytes(t.bytes ?: 0)} present=${t.present}",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
fun GeometryInspectorScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val detail = ui.selectedModel
    ScreenScaffold("Geometry Inspector", nav) {
        ModelPicker(vm, ui.assets.filter { it.kind == "model" })
        if (detail == null) { EmptyState("Open a model to inspect real geometry statistics."); return@ScreenScaffold }
        Section("Mesh / primitive statistics (from the GLB)") { KeyValues(GeometryInspector.rows(detail.geometry)) }
        Spacer(Modifier.height(8.dp))
        Section("Embedded clips (${detail.clips.size})") {
            if (detail.clips.isEmpty()) Text("No animation clips available", style = MaterialTheme.typography.bodySmall)
            detail.clips.forEach { c ->
                Text("${c.name}: ${Formatting.seconds(c.durationSeconds)} · ${c.channelCount} channels · ${c.animatedBones.size} bones",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ModelPicker(vm: StudioViewModel, models: List<com.assetstudio.core.model.AssetSummary>) {
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        models.take(6).forEach { m ->
            FilterChip(selected = false, onClick = { vm.selectModel(m.id) },
                label = { Text(m.name, style = MaterialTheme.typography.labelSmall) })
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ValidationReportScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val report = ui.report
    var selected by remember { mutableStateOf("") }
    ScreenScaffold("Validation Report", nav) {
        Text("Official Khronos glTF Validator diagnostics plus pipeline checks for integrity, extensions, missing textures, geometry, skeletons and conversion.",
            style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            ui.assets.take(8).forEach { a ->
                FilterChip(selected = selected == a.id, onClick = {
                    selected = a.id; vm.loadValidation(a.kind, a.id)
                }, label = { Text(a.name, style = MaterialTheme.typography.labelSmall) })
            }
        }
        Spacer(Modifier.height(8.dp))
        if (report == null) { EmptyState("Select an asset to load its generated PASS/WARNING/ERROR report."); return@ScreenScaffold }
        Section(ValidationInspector.headline(report)) { KeyValues(ValidationInspector.rows(report)) }
        Spacer(Modifier.height(8.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(report.checks) { c ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(10.dp)) {
                        Text("${c.severity} · ${c.code}", style = MaterialTheme.typography.titleSmall,
                            color = if (c.severity == "ERROR") MaterialTheme.colorScheme.error
                            else if (c.severity == "WARNING") MaterialTheme.colorScheme.tertiary
                            else MaterialTheme.colorScheme.primary)
                        Text(c.description, style = MaterialTheme.typography.bodySmall)
                        Text("Asset: ${c.asset} · Location: ${c.location}", style = MaterialTheme.typography.bodySmall)
                        if (c.suggestedFix.isNotBlank()) Text("Fix: ${c.suggestedFix}",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
                    }
                }
            }
        }
    }
}

@Composable
fun DownloadsScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val ctx = LocalContext.current
    val importLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let { vm.importLocal(it, it.lastPathSegment ?: "imported.glb") }
    }
    ScreenScaffold("Downloads", nav) {
        Section("Cache") {
            Text("${ui.cached.size} local assets · ${Formatting.bytes(ui.cacheBytes)}",
                style = MaterialTheme.typography.titleSmall)
            Text("Cached GLBs render offline. Library downloads are atomic and SHA-256 verified.",
                style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(6.dp))
            Button(onClick = { importLauncher.launch(arrayOf("model/gltf-binary", "model/gltf+json", "*/*")) }) {
                Text("Import local GLB/glTF (SAF)")
            }
        }
        when (val d = ui.download) {
            is DownloadState.Progress -> {
                Spacer(Modifier.height(8.dp))
                Text("Downloading ${Formatting.bytes(d.bytesRead)} / ${Formatting.bytes(d.totalBytes)}",
                    style = MaterialTheme.typography.bodySmall)
                LinearProgressIndicator(if (d.totalBytes > 0) d.bytesRead.toFloat() / d.totalBytes else 0f,
                    Modifier.fillMaxWidth())
            }
            is DownloadState.Failed -> Text("Error: ${d.message}", color = MaterialTheme.colorScheme.error)
            else -> Unit
        }
        Spacer(Modifier.height(8.dp))
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(ui.cached) { a ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(a.name, style = MaterialTheme.typography.titleSmall)
                        Text("${a.origin} · ${a.sourceFormat} · ${Formatting.bytes(a.bytes)} · ${a.validationStatus}",
                            style = MaterialTheme.typography.bodySmall)
                        Text("SHA-256 ${a.sha256.take(16)}…", style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            OutlinedButton(onClick = {
                                val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.fileprovider",
                                    File(a.localPath))
                                val share = Intent(Intent.ACTION_SEND).apply {
                                    type = "model/gltf-binary"; putExtra(Intent.EXTRA_STREAM, uri)
                                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                }
                                ctx.startActivity(Intent.createChooser(share, "Share original asset"))
                            }) { Text("Share") }
                            OutlinedButton(onClick = { vm.deleteLocal(a.id) }) { Text("Delete local") }
                            OutlinedButton(onClick = { nav.navigate(Routes.VIEWPORT) }) { Text("Open viewer") }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(vm: StudioViewModel, nav: NavController) {
    val ui by vm.ui.collectAsState()
    val s = ui.settings
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(s?.apiBaseUrl ?: "") }
    LaunchedEffect(s?.apiBaseUrl) { url = s?.apiBaseUrl ?: "" }
    ScreenScaffold("Settings", nav) {
        Section("Static catalog (GitHub Pages, no credentials)") {
            OutlinedTextField(value = url, onValueChange = { url = it },
                label = { Text("Catalog base URL (https://<owner>.github.io/<repo>)") },
                modifier = Modifier.fillMaxWidth(), singleLine = true)
            Text("Metadata and GLB/glTF downloads are plain HTTPS files served by GitHub Pages. " +
                "There is no API server and no API key.",
                style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(6.dp))
            Button(onClick = {
                scope.launch {
                    vm.setSetting(SettingsRepository.CATALOG_BASE_URL, url.trim().trimEnd('/'))
                    vm.refreshCatalog()
                }
            }) { Text("Save and refresh catalog") }
        }
        Spacer(Modifier.height(8.dp))
        Section("Rendering quality (Balanced default)") {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Quality.entries.forEach { q ->
                    FilterChip(selected = (s?.quality ?: Quality.BALANCED) == q, onClick = {
                        scope.launch { vm.setSetting(SettingsRepository.QUALITY, q.name) }
                    }, label = { Text(q.name.lowercase().replaceFirstChar { it.uppercase() }) })
                }
            }
            Text("Default render mode", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                RenderMode.entries.forEach { m ->
                    FilterChip(selected = (s?.renderMode ?: RenderMode.PBR) == m, onClick = {
                        scope.launch { vm.setSetting(SettingsRepository.RENDER_MODE, m.name) }
                    }, label = { Text(m.name, style = MaterialTheme.typography.labelSmall) })
                }
            }
            Text("Exposure ${"%.2f".format(s?.exposure ?: 1f)}", style = MaterialTheme.typography.bodySmall)
            Slider(value = s?.exposure ?: 1f, onValueChange = { v ->
                scope.launch { vm.setSetting(SettingsRepository.EXPOSURE, v) }
            }, valueRange = 0.2f..2.5f)
        }
        Spacer(Modifier.height(8.dp))
        Section("Viewport defaults") {
            val toggles: List<Triple<String, Boolean, androidx.datastore.preferences.core.Preferences.Key<Boolean>>> = listOf(
                Triple("Grid", s?.showGrid ?: true, SettingsRepository.SHOW_GRID),
                Triple("Axes", s?.showAxes ?: true, SettingsRepository.SHOW_AXES),
                Triple("Ground", s?.showGround ?: false, SettingsRepository.SHOW_GROUND),
                Triple("Skeleton overlay", s?.showSkeleton ?: false, SettingsRepository.SHOW_SKELETON),
                Triple("Skeleton-only", s?.skeletonOnly ?: false, SettingsRepository.SKELETON_ONLY),
                Triple("HDR / image-based lighting", s?.hdrEnabled ?: true, SettingsRepository.HDR_ENABLED),
                Triple("Shadows", s?.shadowsEnabled ?: true, SettingsRepository.SHADOWS_ENABLED),
                Triple("Orthographic camera", s?.orthographic ?: false, SettingsRepository.ORTHOGRAPHIC),
                Triple("Thermal-aware frame pacing", s?.thermalAware ?: true, SettingsRepository.THERMAL_AWARE),
                Triple("Large file warnings", s?.largeFileWarning ?: true, SettingsRepository.LARGE_FILE_WARNING),
                Triple("Loop animation", s?.loop ?: true, SettingsRepository.LOOP),
                Triple("FPS overlay", s?.frameRateOverlay ?: false, SettingsRepository.FPS_OVERLAY))
            toggles.forEach { (label, value, pref) ->
                ToggleRow(label, value) { on -> scope.launch { vm.setSetting(pref, on) } }
            }
        }
        Spacer(Modifier.height(8.dp))
        Section("Playback speed") {
            Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                PLAYBACK_SPEEDS.forEach { speed ->
                    FilterChip(selected = (s?.speed ?: 1f) == speed, onClick = {
                        scope.launch { vm.setSetting(SettingsRepository.SPEED, speed) }
                    }, label = { Text("${speed}x", style = MaterialTheme.typography.labelSmall) })
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Section("About / safety") {
            Text("KTX2/Basis textures are disabled unless runtime decoding is available. Uploaded 3D files are treated as untrusted data: type/size validation, no embedded script execution, no geometry reduction by default.",
                style = MaterialTheme.typography.bodySmall)
        }
    }
}
