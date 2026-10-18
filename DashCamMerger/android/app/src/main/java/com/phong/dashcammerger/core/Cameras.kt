package com.phong.dashcammerger.core

import java.util.regex.Pattern

/** Camera detection and grouping (port of core/cameras.py) - FR-2. */

class CameraProfile(
    val name: String,
    /** "name" matches the file name, "parent" matches the containing folder's name. */
    val scope: String,
    val regex: Pattern,
    val map: Map<String, Camera>,
    val hasTs: Boolean = false,
    val hasSeq: Boolean = false,
)

class DetectionResult(
    /** null when no profile reached the threshold. */
    val profileName: String?,
    val matchedRatio: Double,
    val tokens: Map<String, List<ClipInfo>> = emptyMap(),
)

object Cameras {
    private fun ci(re: String): Pattern = Pattern.compile(re, Pattern.CASE_INSENSITIVE)

    /** FR-2.1: built-in profiles, tried in this order. */
    val BUILTIN_PROFILES = listOf(
        CameraProfile(
            "Xforce", "name",
            ci("^(?<ts>\\d{14})_(?<seq>\\d+)(?<cam>[FR])\\.(?:mp4|mov)$"),
            mapOf("F" to Camera.Front, "R" to Camera.Rear),
            hasTs = true, hasSeq = true,
        ),
        CameraProfile(
            "SuffixLetter", "name",
            ci("_(?<cam>[FRABI])\\.[A-Za-z0-9]+$"),
            mapOf(
                "F" to Camera.Front, "A" to Camera.Front, "R" to Camera.Rear,
                "B" to Camera.Rear, "I" to Camera.Interior,
            ),
        ),
        CameraProfile(
            "Word", "name",
            ci("(?<cam>FRONT|REAR|BACK|INSIDE|INTERIOR|CABIN)"),
            mapOf(
                "FRONT" to Camera.Front, "REAR" to Camera.Rear, "BACK" to Camera.Rear,
                "INSIDE" to Camera.Interior, "INTERIOR" to Camera.Interior, "CABIN" to Camera.Interior,
            ),
        ),
        CameraProfile(
            "CamN", "name",
            ci("CAM(?<cam>[1-3])"),
            mapOf("1" to Camera.Front, "2" to Camera.Rear, "3" to Camera.Interior),
        ),
        CameraProfile(
            "70mai", "name",
            ci("^(?:NO|EV|PA)?\\d{4}[-_]?\\d{4}[-_]\\d{6}[-_]\\d+(?<cam>[FB])\\.[A-Za-z0-9]+$"),
            mapOf("F" to Camera.Front, "B" to Camera.Rear),
        ),
        CameraProfile(
            "Folder", "parent",
            ci("^(?<cam>front|f|normal|rear|r|back|b|interior|inside|cabin|i)$"),
            mapOf(
                "FRONT" to Camera.Front, "F" to Camera.Front, "NORMAL" to Camera.Front,
                "REAR" to Camera.Rear, "R" to Camera.Rear, "BACK" to Camera.Rear, "B" to Camera.Rear,
                "INTERIOR" to Camera.Interior, "INSIDE" to Camera.Interior,
                "CABIN" to Camera.Interior, "I" to Camera.Interior,
            ),
        ),
    )

    const val MATCH_THRESHOLD = 0.90

    private val TOKEN_TAIL = Pattern.compile("([A-Za-z]+)\\.[A-Za-z0-9]+$")

    private fun reset(clips: List<ClipInfo>) {
        for (clip in clips) {
            clip.camera = Camera.Unclassified
            clip.cameraToken = ""
        }
    }

    /** Label every clip this profile recognises; return how many matched. */
    fun applyProfile(clips: List<ClipInfo>, profile: CameraProfile): Int {
        var matched = 0
        for (clip in clips) {
            val subject = if (profile.scope == "name") clip.name else clip.parentName
            val m = profile.regex.matcher(subject)
            if (!m.find()) continue
            val token = (m.group("cam") ?: "").uppercase()
            val label = profile.map[token] ?: continue
            clip.camera = label
            clip.cameraToken = token
            matched++
            // Reuse what the profile already parsed instead of a second pass.
            if (profile.hasTs) m.group("ts")?.let { clip.timestamp = Ordering.parseTimestamp14(it) }
            if (profile.hasSeq) m.group("seq")?.let { clip.sequence = it.toLongOrNull() }
        }
        return matched
    }

    /** Group clips by a guessed token for the manual mapping screen (FR-2.3). */
    fun extractTokens(clips: List<ClipInfo>): Map<String, List<ClipInfo>> {
        val tokens = sortedMapOf<String, MutableList<ClipInfo>>()
        val useFolder = clips.map { it.parentName }.toSet().size > 1
        for (clip in clips) {
            val token = if (useFolder) {
                "folder:" + clip.parentName
            } else {
                val m = TOKEN_TAIL.matcher(clip.name)
                if (m.find()) m.group(1).uppercase() else "ALL"
            }
            tokens.getOrPut(token) { mutableListOf() }.add(clip)
        }
        return tokens
    }

    /** Pick the first profile matching >= 90% of the clips (FR-2.2). */
    fun detect(clips: List<ClipInfo>): DetectionResult {
        if (clips.isEmpty()) return DetectionResult(null, 0.0)
        var best = 0.0
        for (profile in BUILTIN_PROFILES) {
            reset(clips)
            val ratio = applyProfile(clips, profile).toDouble() / clips.size
            best = maxOf(best, ratio)
            if (ratio >= MATCH_THRESHOLD) return DetectionResult(profile.name, ratio)
        }
        reset(clips)
        return DetectionResult(null, best, extractTokens(clips))
    }

    /** Assign labels chosen by the user (FR-2.3). */
    fun applyManualMap(tokens: Map<String, List<ClipInfo>>, mapping: Map<String, Camera>) {
        for ((token, tokenClips) in tokens) {
            val label = mapping[token] ?: Camera.Unclassified
            for (clip in tokenClips) {
                clip.camera = label
                clip.cameraToken = token
            }
        }
    }

    /** Build Front/Rear/Interior groups plus the Unclassified leftovers. */
    fun groupByCamera(clips: List<ClipInfo>): Pair<List<CameraGroup>, List<ClipInfo>> {
        val groups = CAMERA_ORDER.mapNotNull { label ->
            val members = clips.filter { it.camera == label }
            if (members.isEmpty()) null else CameraGroup(label, members.toMutableList())
        }
        return groups to clips.filter { it.camera == Camera.Unclassified }
    }
}
