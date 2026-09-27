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
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phong.dashcammerger.core.Camera
import com.phong.dashcammerger.core.PlanOptions

private const val PREFS = "dashcam_merger"
private const val KEY_TREE = "tree_uri"
private const val KEY_OUT_TREE = "output_tree_uri"
private const val KEY_SPLIT_GAP = "split_on_gap"
private const val KEY_SPLIT_FAT32 = "split_fat32"

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
    var splitOnGap by remember { mutableStateOf(prefs.getBoolean(KEY_SPLIT_GAP, false)) }
    var splitFat32 by remember { mutableStateOf(prefs.getBoolean(KEY_SPLIT_FAT32, false)) }
    val session = MergeSession

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

    Column(
        Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("DashCam Merger", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
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
                OptionRow("Start a new file after a trip gap (> 30 min)", splitOnGap, !session.busy) {
                    splitOnGap = it
                    prefs.edit().putBoolean(KEY_SPLIT_GAP, it).apply()
                }
                OptionRow("Split at 3.9 GB (FAT32 SD card / USB stick)", splitFat32, !session.busy) {
                    splitFat32 = it
                    prefs.edit().putBoolean(KEY_SPLIT_FAT32, it).apply()
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val t = tree
            Button(
                onClick = {
                    if (t != null) {
                        session.scan(
                            ctx, t, effectiveOut,
                            PlanOptions(splitOnGap = splitOnGap, maxPartGb = if (splitFat32) FAT32_PART_GB else 0.0),
                        )
                    }
                },
                enabled = t != null && !session.busy,
            ) { Text("Scan & plan") }
            when (session.phase) {
                Phase.Planned -> Button(onClick = { session.merge(ctx) }) { Text("Merge") }
                Phase.Merging, Phase.Scanning ->
                    OutlinedButton(onClick = { session.cancel() }, enabled = session.phase == Phase.Merging) { Text("Cancel") }
                else -> Unit
            }
        }

        if (session.status.isNotEmpty()) {
            Text(session.status, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        }
        if (session.busy) {
            LinearProgressIndicator(progress = { session.progress }, modifier = Modifier.fillMaxWidth())
        }

        if (session.phase == Phase.NeedCameraMap) {
            CameraMapper(onApply = { session.applyCameraMap(ctx, it) })
        } else if (session.text.isNotEmpty()) {
            Card(Modifier.fillMaxWidth().weight(1f)) {
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

@Composable
private fun OptionRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
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
