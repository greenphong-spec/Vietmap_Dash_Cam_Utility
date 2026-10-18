package com.phong.dashcammerger.android

import android.content.Context
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import com.phong.dashcammerger.core.ClipInfo
import com.phong.dashcammerger.core.ProbeInfo
import com.phong.dashcammerger.core.StreamSignature
import java.nio.ByteBuffer

/**
 * Stream inspection with Android's MediaExtractor (replaces the desktop's ffprobe) - FR-4.
 * Only codecs MediaMuxer can place in an MP4 without re-encoding are accepted.
 */
object MediaProbe {
    private val MUXABLE_VIDEO = setOf(
        MediaFormat.MIMETYPE_VIDEO_AVC,
        MediaFormat.MIMETYPE_VIDEO_HEVC,
        MediaFormat.MIMETYPE_VIDEO_MPEG4,
        MediaFormat.MIMETYPE_VIDEO_H263,
    )
    private val MUXABLE_AUDIO = setOf(
        MediaFormat.MIMETYPE_AUDIO_AAC,
        MediaFormat.MIMETYPE_AUDIO_AMR_NB,
        MediaFormat.MIMETYPE_AUDIO_AMR_WB,
    )
    private val FRIENDLY = mapOf(
        MediaFormat.MIMETYPE_VIDEO_AVC to "H.264",
        MediaFormat.MIMETYPE_VIDEO_HEVC to "H.265",
        MediaFormat.MIMETYPE_VIDEO_MPEG4 to "MPEG-4",
        MediaFormat.MIMETYPE_VIDEO_H263 to "H.263",
        MediaFormat.MIMETYPE_AUDIO_AAC to "AAC",
        MediaFormat.MIMETYPE_AUDIO_AMR_NB to "AMR",
        MediaFormat.MIMETYPE_AUDIO_AMR_WB to "AMR-WB",
    )

    /** Track indexes to copy; audio = -1 when there is none or it cannot go into MP4. */
    class Tracks(val video: Int, val audio: Int, val videoMime: String?, val droppedAudioMime: String?)

    fun selectTracks(ex: MediaExtractor): Tracks {
        var video = -1
        var audio = -1
        var videoMime: String? = null
        var dropped: String? = null
        for (i in 0 until ex.trackCount) {
            val mime = ex.getTrackFormat(i).getString(MediaFormat.KEY_MIME) ?: continue
            when {
                mime.startsWith("video/") && video < 0 -> { video = i; videoMime = mime }
                mime.startsWith("audio/") && audio < 0 && dropped == null ->
                    if (mime in MUXABLE_AUDIO) audio = i else dropped = mime
            }
        }
        return Tracks(video, audio, videoMime, dropped)
    }

    private fun csdHash(f: MediaFormat): String {
        val parts = listOf("csd-0", "csd-1", "csd-2").mapNotNull { key ->
            f.getByteBuffer(key)?.let { buf: ByteBuffer ->
                val dup = buf.duplicate().apply { rewind() }
                ByteArray(dup.remaining()).also { dup.get(it) }.contentHashCode()
            }
        }
        return parts.joinToString("/") { Integer.toHexString(it) }
    }

    private fun MediaFormat.intOr(key: String, default: Int) = if (containsKey(key)) getInteger(key) else default
    private fun MediaFormat.longOr(key: String, default: Long) = if (containsKey(key)) getLong(key) else default

    /** Fill in clip.probe, or clip.unreadableReason when the file cannot be stream-copied (FR-4.5). */
    fun probe(ctx: Context, clip: ClipInfo) {
        val ex = MediaExtractor()
        try {
            ex.setDataSource(ctx, Uri.parse(clip.uri), null)
            val t = selectTracks(ex)
            if (t.video < 0) {
                clip.unreadableReason = "no video track"
                return
            }
            if (t.videoMime !in MUXABLE_VIDEO) {
                clip.unreadableReason = "video codec ${t.videoMime} cannot be stream-copied to MP4"
                return
            }
            val v = ex.getTrackFormat(t.video)
            val w = v.intOr(MediaFormat.KEY_WIDTH, 0)
            val h = v.intOr(MediaFormat.KEY_HEIGHT, 0)
            val rot = v.intOr(MediaFormat.KEY_ROTATION, 0)
            var duration = v.longOr(MediaFormat.KEY_DURATION, 0)
            val videoSig = "${t.videoMime} ${w}x$h rot$rot csd:${csdHash(v)}"

            var audioSig = "none"
            var audioDesc = ""
            if (t.audio >= 0) {
                val a = ex.getTrackFormat(t.audio)
                val mime = a.getString(MediaFormat.KEY_MIME)
                audioSig = "$mime ${a.intOr(MediaFormat.KEY_SAMPLE_RATE, 0)}Hz " +
                    "${a.intOr(MediaFormat.KEY_CHANNEL_COUNT, 0)}ch csd:${csdHash(a)}"
                audioDesc = " + " + (FRIENDLY[mime] ?: mime)
                duration = maxOf(duration, a.longOr(MediaFormat.KEY_DURATION, 0))
            }
            if (duration <= 0) {
                clip.unreadableReason = "no duration (file may be truncated)"
                return
            }
            clip.probe = ProbeInfo(
                durationUs = duration,
                signature = StreamSignature(videoSig, audioSig),
                description = "${FRIENDLY[t.videoMime] ?: t.videoMime} ${w}x$h$audioDesc",
                audioDropped = t.droppedAudioMime,
            )
        } catch (e: Exception) {
            clip.unreadableReason = e.message ?: e.javaClass.simpleName
        } finally {
            ex.release()
        }
    }

    /** Duration of a finished output file, in microseconds. */
    fun outputDurationUs(ctx: Context, uri: Uri): Long {
        val ex = MediaExtractor()
        try {
            ex.setDataSource(ctx, uri, null)
            return (0 until ex.trackCount).maxOfOrNull { ex.getTrackFormat(it).longOr(MediaFormat.KEY_DURATION, 0) } ?: 0
        } finally {
            ex.release()
        }
    }
}
