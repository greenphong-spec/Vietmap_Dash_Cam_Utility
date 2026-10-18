package com.phong.dashcammerger.android

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.DocumentsContract
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TriStateCheckbox
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phong.dashcammerger.core.CAMERA_ORDER
import com.phong.dashcammerger.core.Camera
import com.phong.dashcammerger.core.PlanOptions
import com.phong.dashcammerger.core.Report

private const val PREFS = "dashcam_merger"
private const val KEY_TREE = "tree_uri"
private const val KEY_OUT_TREE = "output_tree_uri"
private const val KEY_SPLIT_GAP = "split_on_gap"
private const val KEY_TRIP_GAP_MIN = "trip_gap_minutes"
private const val KEY_SPLIT_FAT32 = "split_fat32"
private const val KEY_ORDER_BY_TIME = "order_by_time"
private const val KEY_LINK_PAIRS = "link_pairs"

private const val TAB_SETUP = 0
private const val TAB_FILES = 1
private const val TAB_DETAILS = 2

/** FR-3.5: a break of more than this many minutes starts a new trip, unless changed. */
private const val DEFAULT_TRIP_GAP_MIN = 15

/** Just under FAT32's 4 GiB file limit, for SD cards formatted by the dashcam. */
private const val FAT32_PART_GB = 3.9

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            val scheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()
            MaterialTheme(colorScheme = scheme) {
                Surface(Modifier.fillMaxSize()) { MergerScreen() }
            }
        }
    }
}

private fun sameTree(a: Uri?, b: Uri?): Boolean = try {
    a != null && b != null && DocumentsContract.getTreeDocumentId(a) == DocumentsContract.getTreeDocumentId(b)
} catch (_: Exception) {
    false
}

@Composable
private fun MergerScreen() {
    val ctx = LocalContext.current
    val prefs = remember { ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    var tree by remember { mutableStateOf(prefs.getString(KEY_TREE, null)?.let(Uri::parse)) }
    var outTree by remember { mutableStateOf(prefs.getString(KEY_OUT_TREE, null)?.let(Uri::parse)) }
    var splitOnGap by remember { mutableStateOf(prefs.getBoolean(KEY_SPLIT_GAP, true)) }
    var tripGapText by remember {
        mutableStateOf(prefs.getInt(KEY_TRIP_GAP_MIN, DEFAULT_TRIP_GAP_MIN).toString())
    }
    var splitFat32 by remember { mutableStateOf(prefs.getBoolean(KEY_SPLIT_FAT32, false)) }
    var linkPairs by remember { mutableStateOf(prefs.getBoolean(KEY_LINK_PAIRS, false)) }
    val session = MergeSession
    remember { session.arrange(prefs.getBoolean(KEY_ORDER_BY_TIME, true)) }

    // Merging several GB takes minutes; don't let the screen timeout pause it.
    val view = LocalView.current
    DisposableEffect(session.busy) {
        view.keepScreenOn = session.busy
        onDispose { view.keepScreenOn = false }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            ctx.contentResolver.takePersistableUriPermission(
                uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            prefs.edit().putString(KEY_TREE, uri.toString()).apply()
            tree = uri
            session.reset()
        }
    }
    val outPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            ctx.contentResolver.takePersistableUriPermission(
                uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            prefs.edit().putString(KEY_OUT_TREE, uri.toString()).apply()
            outTree = uri
            session.reset()
        }
    }
    // Picking the input folder itself as output means "the default merged/ subfolder".
    val effectiveOut = outTree?.takeUnless { sameTree(it, tree) }

    // AR-12.1: three tabs so the file list and the text can use the whole screen.
    var tab by rememberSaveable { mutableIntStateOf(TAB_SETUP) }
    // AR-12.3: jump to the tab that needs the user's attention.
    LaunchedEffect(session.phase) {
        when (session.phase) {
            Phase.NeedCameraMap -> tab = TAB_SETUP
            Phase.Planned -> tab = TAB_FILES
            Phase.Done, Phase.Error -> tab = TAB_DETAILS
            else -> Unit
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("DashCam Merger", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)

        val parts = session.planParts
        TabRow(selectedTabIndex = tab) {
            Tab(selected = tab == TAB_SETUP, onClick = { tab = TAB_SETUP }, text = { Text("Setup") })
            Tab(
                selected = tab == TAB_FILES, onClick = { tab = TAB_FILES },
                text = { Text(if (parts.isEmpty()) "Files" else "Files ${session.selected.size}/${parts.size}", maxLines = 1) },
            )
            Tab(selected = tab == TAB_DETAILS, onClick = { tab = TAB_DETAILS }, text = { Text("Details") })
        }

        Box(Modifier.fillMaxWidth().weight(1f)) {
            when (tab) {
                TAB_SETUP -> if (session.phase == Phase.NeedCameraMap) {
                    CameraMapper(onApply = { session.applyCameraMap(ctx, it) })
                } else {
                    Column(
                        Modifier.fillMaxSize().verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            "Joins dashcam clips per camera into one video, without re-encoding. Source files are never changed or deleted.",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Input (dashcam clips)", style = MaterialTheme.typography.labelMedium)
                                Text(
                                    tree?.let { SafScanner.describeTree(ctx, it) } ?: "No folder chosen",
                                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                                )
                                OutlinedButton(onClick = { picker.launch(null) }, enabled = !session.busy) {
                                    Text(if (tree == null) "Choose dashcam folder" else "Change input folder")
                                }

                                Text("Output (merged videos)", style = MaterialTheme.typography.labelMedium)
                                Text(
                                    effectiveOut?.let { SafScanner.describeTree(ctx, it) } ?: "Same as input, in a \"merged\" subfolder",
                                    style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                                )
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    OutlinedButton(onClick = { outPicker.launch(null) }, enabled = !session.busy) {
                                        Text("Choose output folder")
                                    }
                                    if (outTree != null) {
                                        OutlinedButton(
                                            onClick = {
                                                prefs.edit().remove(KEY_OUT_TREE).apply()
                                                outTree = null
                                                session.reset()
                                            },
                                            enabled = !session.busy,
                                        ) { Text("Use default") }
                                    }
                                }
                                OptionRow("Split into trips (one file per trip)", splitOnGap, !session.busy) {
                                    splitOnGap = it
                                    prefs.edit().putBoolean(KEY_SPLIT_GAP, it).apply()
                                }
                                // FR-3.5b: the trip gap, remembered; anything not a whole number > 0 means 15.
                                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                                    Text(
                                        "New trip after a break of more than",
                                        style = MaterialTheme.typography.bodySmall,
                                        modifier = Modifier.weight(1f),
                                    )
                                    OutlinedTextField(
                                        value = tripGapText,
                                        onValueChange = { text ->
                                            tripGapText = text.filter { it.isDigit() }.take(4)
                                            tripGapText.toIntOrNull()?.takeIf { it > 0 }?.let {
                                                prefs.edit().putInt(KEY_TRIP_GAP_MIN, it).apply()
                                            }
                                        },
                                        enabled = splitOnGap && !session.busy,
                                        singleLine = true,
                                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                                        textStyle = MaterialTheme.typography.bodySmall,
                                        modifier = Modifier.width(72.dp),
                                    )
                                    Spacer(Modifier.width(6.dp))
                                    Text("min", style = MaterialTheme.typography.bodySmall)
                                }
                                OptionRow("Split at 3.9 GB (FAT32 SD card / USB stick)", splitFat32, !session.busy) {
                                    splitFat32 = it
                                    prefs.edit().putBoolean(KEY_SPLIT_FAT32, it).apply()
                                }
                            }
                        }
                    }
                }

                TAB_FILES -> if (parts.isEmpty()) {
                    Text("No plan yet - tap Scan & plan to list the files to export.", style = MaterialTheme.typography.bodyMedium)
                } else {
                    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                        PartSelector(
                            enabled = session.phase == Phase.Planned,
                            linkPairs = linkPairs,
                            onLinkPairs = {
                                linkPairs = it
                                prefs.edit().putBoolean(KEY_LINK_PAIRS, it).apply()
                            },
                            onOrderByTime = {
                                session.arrange(it)
                                prefs.edit().putBoolean(KEY_ORDER_BY_TIME, it).apply()
                            },
                        )
                    }
                }

                else -> if (session.text.isEmpty()) {
                    Text("The plan, the merge report and any errors appear here.", style = MaterialTheme.typography.bodyMedium)
                } else {
                    Card(Modifier.fillMaxSize()) {
                        Text(
                            session.text,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            lineHeight = 15.sp,
                            modifier = Modifier
                                .padding(12.dp)
                                .verticalScroll(rememberScrollState())
                                .horizontalScroll(rememberScrollState()),
                        )
                    }
                }
            }
        }

        // AR-12.2: actions and progress stay visible whichever tab is open.
        if (session.status.isNotEmpty()) {
            Text(session.status, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        }
        if (session.busy) {
            LinearProgressIndicator(progress = { session.progress }, modifier = Modifier.fillMaxWidth())
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val t = tree
            Button(
                onClick = {
                    if (t != null) {
                        session.scan(
                            ctx, t, effectiveOut,
                            PlanOptions(
                                splitOnGap = splitOnGap,
                                gapSplitSeconds = 60 * (tripGapText.toIntOrNull()?.takeIf { it > 0 } ?: DEFAULT_TRIP_GAP_MIN),
                                maxPartGb = if (splitFat32) FAT32_PART_GB else 0.0,
                            ),
                        )
                    }
                },
                enabled = t != null && !session.busy,
            ) { Text("Scan & plan") }
            when (session.phase) {
                Phase.Planned ->
                    Button(onClick = { session.merge(ctx) }, enabled = session.selected.isNotEmpty()) {
                        Text("Merge ${session.selected.size} file(s)")
                    }
                Phase.Merging, Phase.Scanning ->
                    OutlinedButton(onClick = { session.cancel() }, enabled = session.phase == Phase.Merging) { Text("Cancel") }
                else -> Unit
            }
        }
    }
}

@Composable
private fun OptionRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}

/**
 * AR-10.5-10.9, 10.14-10.17: tick which planned output files to export (all ticked after a
 * scan), choose their order (by camera / by time / manual ▲▼) and optionally keep the
 * Front and Rear file of the same time ticked together.
 */
@Composable
private fun PartSelector(enabled: Boolean, linkPairs: Boolean, onLinkPairs: (Boolean) -> Unit, onOrderByTime: (Boolean) -> Unit) {
    val session = MergeSession
    val parts = session.planParts
    val order = session.order
    val chosen = session.selectedParts
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Files to export", style = MaterialTheme.typography.labelMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { session.selectAll() }, enabled = enabled && chosen.size < parts.size) { Text("Select all") }
                OutlinedButton(onClick = { session.selectNone() }, enabled = enabled && chosen.isNotEmpty()) { Text("Select none") }
            }
            Text(
                Report.selectionSummary(chosen, parts.size),
                style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
            )
            val warning = Report.spaceWarning(chosen.sumOf { it.expectedSize }, session.freeBytes)
            if (warning.isNotEmpty()) {
                Text(warning, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
            }

            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Order:", style = MaterialTheme.typography.bodySmall)
                FilterChip(selected = !session.orderByTime, enabled = enabled, onClick = { onOrderByTime(false) }, label = { Text("By camera") })
                FilterChip(selected = session.orderByTime, enabled = enabled, onClick = { onOrderByTime(true) }, label = { Text("By time") })
            }
            OptionRow("Keep Front/Rear pairs together (same time)", linkPairs, enabled, onLinkPairs)

            // Per-camera tri-state boxes (AR-10.7).
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                for (cam in CAMERA_ORDER) {
                    val indices = parts.indices.filter { parts[it].camera == cam }
                    if (indices.isEmpty()) continue
                    val ticked = indices.count { it in session.selected }
                    val state = when (ticked) {
                        0 -> ToggleableState.Off
                        indices.size -> ToggleableState.On
                        else -> ToggleableState.Indeterminate
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        TriStateCheckbox(state = state, enabled = enabled, onClick = { session.setCamera(cam, state != ToggleableState.On) })
                        Text("${cam.name} ($ticked/${indices.size})", style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold)
                    }
                }
            }

            for ((pos, i) in order.withIndex()) {
                val part = parts[i]
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                    Checkbox(checked = i in session.selected, enabled = enabled, onCheckedChange = { session.toggle(i, linkPairs) })
                    Column(Modifier.weight(1f)) {
                        val label = Report.partLabel(part)
                        Text(
                            if (label.isEmpty()) part.camera.name else "${part.camera.name} $label",
                            style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Bold,
                        )
                        Report.partSpan(part).takeIf { it.isNotEmpty() }?.let {
                            Text(it, style = MaterialTheme.typography.bodySmall)
                        }
                        Text(
                            "${part.clips.size} clips, ${Report.formatDuration(part.expectedDurationUs)}, " +
                                Report.formatSize(part.expectedSize),
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Text(part.outputName, style = MaterialTheme.typography.bodySmall)
                    }
                    IconButton(onClick = { session.move(pos, -1) }, enabled = enabled && pos > 0) { Text("▲") }
                    IconButton(onClick = { session.move(pos, 1) }, enabled = enabled && pos < order.size - 1) { Text("▼") }
                }
            }
        }
    }
}

/** FR-2.3: manual camera assignment when no built-in naming profile matched. */
@Composable
private fun CameraMapper(onApply: (Map<String, Camera>) -> Unit) {
    val tokens = MergeSession.tokens
    val choice = remember(tokens) { mutableStateMapOf<String, Camera>() }
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        for ((token, clips) in tokens) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(10.dp)) {
                    Text("$token  (${clips.size} files, e.g. ${clips.first().name})", style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        for (cam in listOf(Camera.Front, Camera.Rear, Camera.Interior, Camera.Skip)) {
                            FilterChip(
                                selected = choice[token] == cam,
                                onClick = { choice[token] = cam },
                                label = { Text(cam.name) },
                            )
                        }
                    }
                }
            }
        }
        Button(onClick = { onApply(choice.toMap()) }, enabled = choice.isNotEmpty()) { Text("Apply and plan") }
    }
}
