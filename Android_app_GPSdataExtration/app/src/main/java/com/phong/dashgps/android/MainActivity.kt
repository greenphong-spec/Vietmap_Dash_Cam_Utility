package com.phong.dashgps.android

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phong.dashgps.core.ExportFormat
import com.phong.dashgps.core.OverlayProfiles
import com.phong.dashgps.core.Track
import kotlin.math.cos

private const val PREFS = "dashgps"
private const val KEY_OUT = "export_tree_uri"
private const val KEY_PROFILE = "profile"
private const val KEY_INTERVAL = "interval"
private const val KEY_FORMATS = "formats"
private const val KEY_COMBINE = "combine"
private const val KEY_GE_EXTRAS = "ge_extras"

private val INTERVALS = listOf(1.0, 2.0, 5.0, 10.0)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
                Surface(Modifier.fillMaxSize()) { ExtractorScreen() }
            }
        }
    }
}

private fun formatSize(bytes: Long): String = when {
    bytes >= 1L shl 30 -> String.format(java.util.Locale.US, "%.2f GB", bytes / (1024.0 * 1024 * 1024))
    else -> String.format(java.util.Locale.US, "%.1f MB", bytes / (1024.0 * 1024))
}

@Composable
private fun ExtractorScreen() {
    val ctx = LocalContext.current
    val prefs = remember { ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }
    val s = ExtractSession

    var videos by remember { mutableStateOf<List<VideoItem>>(emptyList()) }
    var outTree by remember { mutableStateOf(prefs.getString(KEY_OUT, null)?.let(Uri::parse)) }
    var profile by remember { mutableStateOf(OverlayProfiles.byId(prefs.getString(KEY_PROFILE, null))) }
    var interval by remember { mutableStateOf(prefs.getFloat(KEY_INTERVAL, 1f).toDouble()) }
    var formats by remember {
        mutableStateOf(
            prefs.getStringSet(KEY_FORMATS, null)?.mapNotNull { n -> ExportFormat.entries.find { it.name == n } }?.toSet()
                ?: setOf(ExportFormat.KMZ, ExportFormat.GPX)
        )
    }
    var combine by remember { mutableStateOf(prefs.getBoolean(KEY_COMBINE, true)) }
    var geExtras by remember { mutableStateOf(prefs.getBoolean(KEY_GE_EXTRAS, false)) }

    val view = LocalView.current
    DisposableEffect(s.busy) {
        view.keepScreenOn = s.busy
        onDispose { view.keepScreenOn = false }
    }

    val videoPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) {
            videos = uris.map { Storage.videoItem(ctx, it) }.sortedBy { it.name.lowercase() }
            s.reset()
        }
    }
    val folderPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            ctx.contentResolver.takePersistableUriPermission(
                uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            prefs.edit().putString(KEY_OUT, uri.toString()).apply()
            outTree = uri
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("DashCam GPS Extractor", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text(
            "Reads the date, time, speed and GPS position printed on dashcam video and saves the route for Google Earth or any map app.",
            style = MaterialTheme.typography.bodySmall,
        )

        // 1. Videos
        Section("1. Videos") {
            OutlinedButton(onClick = { videoPicker.launch(arrayOf("video/*")) }, enabled = !s.busy) {
                Text(if (videos.isEmpty()) "Choose video(s)" else "Change videos")
            }
            if (videos.isEmpty()) {
                Text("Pick one or more front-camera videos (original clips or merged files).", style = MaterialTheme.typography.bodySmall)
            } else {
                Text("${videos.size} video(s), ${formatSize(videos.sumOf { it.sizeBytes })}", fontWeight = FontWeight.Medium)
                videos.take(6).forEach { Text("• ${it.name}", style = MaterialTheme.typography.bodySmall) }
                if (videos.size > 6) Text("… and ${videos.size - 6} more", style = MaterialTheme.typography.bodySmall)
            }
        }

        // 2. Camera and sampling
        Section("2. Camera") {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OverlayProfiles.ALL.forEach { p ->
                    FilterChip(
                        selected = p.id == profile.id,
                        onClick = { profile = p; prefs.edit().putString(KEY_PROFILE, p.id).apply() },
                        label = { Text(p.name) },
                        enabled = !s.busy,
                    )
                }
            }
            if (profile.note.isNotEmpty()) Text(profile.note, style = MaterialTheme.typography.bodySmall)
            Text("Read one frame every:", style = MaterialTheme.typography.labelMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                INTERVALS.forEach { sec ->
                    FilterChip(
                        selected = interval == sec,
                        onClick = { interval = sec; prefs.edit().putFloat(KEY_INTERVAL, sec.toFloat()).apply() },
                        label = { Text("${sec.toInt()} s") },
                        enabled = !s.busy,
                    )
                }
            }
            Text("Camera clock time zone: ${s.zone}", style = MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = { s.testFrame(ctx, videos.first(), profile, 10.0) },
                    enabled = videos.isNotEmpty() && !s.busy,
                ) { Text("Test frame") }
                Button(
                    onClick = { s.extract(ctx, videos, profile, interval) },
                    enabled = videos.isNotEmpty() && !s.busy,
                ) { Text("Extract GPS") }
                if (s.phase == Phase.Extracting) OutlinedButton(onClick = { s.cancel() }) { Text("Cancel") }
            }
        }

        if (s.status.isNotEmpty()) Text(s.status, fontWeight = FontWeight.Medium, style = MaterialTheme.typography.bodyMedium)
        if (s.phase == Phase.Extracting) {
            LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth())
        }

        s.test?.let { t ->
            Section("Test frame at ${t.posSec.toInt()} s") {
                Image(
                    t.bitmap.asImageBitmap(), contentDescription = "Overlay region",
                    modifier = Modifier.fillMaxWidth(), contentScale = ContentScale.FillWidth,
                )
                Text("OCR: ${t.text.ifEmpty { "(nothing)" }}", fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                Text("Parsed: ${t.parsed}", fontWeight = FontWeight.Medium)
            }
        }

        s.track?.let { t ->
            Section("3. Result") {
                if (t.fixes.size >= 2) TrackPreview(t)
                Text(s.summary, fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 15.sp)
            }
            if (t.fixes.isNotEmpty()) {
                Section("4. Save") {
                    Text(outTree?.let { Storage.describeTree(it) } ?: "No folder chosen", fontWeight = FontWeight.Medium)
                    OutlinedButton(onClick = { folderPicker.launch(null) }, enabled = !s.busy) {
                        Text(if (outTree == null) "Choose save folder" else "Change folder")
                    }
                    ExportFormat.entries.forEach { f ->
                        CheckRow(f.label, f in formats, !s.busy) { on ->
                            formats = if (on) formats + f else formats - f
                            prefs.edit().putStringSet(KEY_FORMATS, formats.map { it.name }.toSet()).apply()
                        }
                    }
                    if (ExportFormat.KMZ in formats || ExportFormat.KML in formats) {
                        CheckRow("KMZ/KML: add Google Earth extras (time slider, speed labels)", geExtras, !s.busy) {
                            geExtras = it
                            prefs.edit().putBoolean(KEY_GE_EXTRAS, it).apply()
                        }
                        if (geExtras) {
                            Text(
                                "Other map apps (e.g. Geo Tracker) show the extras as many extra points.",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                    if (videos.size > 1) {
                        CheckRow("Combine all videos into one route", combine, !s.busy) {
                            combine = it
                            prefs.edit().putBoolean(KEY_COMBINE, it).apply()
                        }
                    }
                    Button(
                        onClick = { outTree?.let { s.export(ctx, it, formats, combine, geExtras) } },
                        enabled = outTree != null && formats.isNotEmpty() && !s.busy,
                    ) { Text("Save files") }
                }
            }
        }

        if (s.exported.isNotEmpty()) {
            Section("Saved") {
                s.exported.forEach { Text("• ${it.name}", style = MaterialTheme.typography.bodySmall) }
                val map = s.exported.firstOrNull { it.format == ExportFormat.KMZ }
                    ?: s.exported.firstOrNull { it.format == ExportFormat.KML }
                    ?: s.exported.firstOrNull { it.format == ExportFormat.GPX }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (map != null) Button(onClick = { openInMapApp(ctx, map) }) { Text("Open in Google Earth / map") }
                    OutlinedButton(onClick = { share(ctx, s.exported) }) { Text("Share") }
                }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            content()
        }
    }
}

@Composable
private fun CheckRow(label: String, checked: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    // The whole row (box and label) toggles.
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .toggleable(value = checked, enabled = enabled, role = Role.Checkbox, onValueChange = onChange),
    ) {
        Checkbox(checked = checked, onCheckedChange = null, enabled = enabled)
        Text(label, style = MaterialTheme.typography.bodyMedium)
    }
}

/** Route outline drawn without map tiles (equirectangular, north up), green start, red end. */
@Composable
private fun TrackPreview(track: Track) {
    val line = MaterialTheme.colorScheme.primary
    Canvas(Modifier.fillMaxWidth().height(220.dp)) {
        val pts = track.fixes
        val minLat = pts.minOf { it.lat }
        val maxLat = pts.maxOf { it.lat }
        val minLon = pts.minOf { it.lon }
        val maxLon = pts.maxOf { it.lon }
        val kx = cos(Math.toRadians((minLat + maxLat) / 2))
        val spanX = ((maxLon - minLon) * kx).coerceAtLeast(1e-6)
        val spanY = (maxLat - minLat).coerceAtLeast(1e-6)
        val pad = 12f
        val scale = minOf((size.width - 2 * pad) / spanX, (size.height - 2 * pad) / spanY)
        val ox = (size.width - spanX * scale) / 2
        val oy = (size.height - spanY * scale) / 2
        fun at(lat: Double, lon: Double) =
            Offset((ox + (lon - minLon) * kx * scale).toFloat(), (oy + (maxLat - lat) * scale).toFloat())
        for (seg in track.segments) {
            val path = Path()
            seg.forEachIndexed { i, f ->
                val p = at(f.lat, f.lon)
                if (i == 0) path.moveTo(p.x, p.y) else path.lineTo(p.x, p.y)
            }
            drawPath(path, line, style = Stroke(width = 5f))
        }
        drawCircle(Color(0xFF2E7D32), 10f, at(pts.first().lat, pts.first().lon))
        drawCircle(Color(0xFFC62828), 10f, at(pts.last().lat, pts.last().lon))
    }
}

private fun openInMapApp(ctx: Context, file: ExportedFile) {
    val view = Intent(Intent.ACTION_VIEW).setDataAndType(file.uri, file.format.mime)
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    try {
        ctx.startActivity(Intent.createChooser(view, "Open ${file.name} with"))
    } catch (_: ActivityNotFoundException) {
        Toast.makeText(ctx, "No app can open ${file.format.ext.uppercase()} files. Install Google Earth.", Toast.LENGTH_LONG).show()
    }
}

private fun share(ctx: Context, files: List<ExportedFile>) {
    val send = Intent(Intent.ACTION_SEND_MULTIPLE)
        .setType("*/*")
        .putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(files.map { it.uri }))
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    ctx.startActivity(Intent.createChooser(send, "Share route files"))
}
