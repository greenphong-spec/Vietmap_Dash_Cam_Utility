package com.phong.dashcammerger.android

import android.content.Context
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.net.Uri
import android.os.SystemClock
import com.phong.dashcammerger.core.INCOMPLETE_PREFIX
import com.phong.dashcammerger.core.OutputPart
import com.phong.dashcammerger.core.PartResult
import com.phong.dashcammerger.core.PartStatus
import com.phong.dashcammerger.core.Planner
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.abs

/**
 * Lossless join of one output part (replaces concat.py + verify.py on desktop).
 *
 * Every compressed sample is copied unchanged from MediaExtractor into MediaMuxer;
 * only the timestamps are shifted so each clip starts where the previous one ended.
 * Nothing is decoded or re-encoded, so quality is identical to the source.
 */
object Merger {
    private const val BUFFER_BYTES = 16 * 1024 * 1024 // large enough for a 4K keyframe

    // FR-7.3 tolerances, same as the desktop version.
    private const val TOLERANCE_ABS_US = 1_000_000L
    private const val TOLERANCE_REL = 0.001
    private const val SIZE_MIN_RATIO = 0.90

    private class CopyOutcome(val error: String?, val cancelled: Boolean)

    fun mergePart(
        ctx: Context,
        part: OutputPart,
        out: OutputFolder,
        cancel: AtomicBoolean,
        onBytes: (Long) -> Unit,
    ): PartResult {
        val started = SystemClock.elapsedRealtime()
        fun elapsed() = SystemClock.elapsedRealtime() - started

        val partialUri = out.createFile(part.partialName, "video/mp4")
        val outcome = copyAll(ctx, part, partialUri, cancel, onBytes)

        if (outcome.cancelled || outcome.error != null) {
            val kept = keepIncomplete(out, partialUri, part)
            return PartResult(
                part, if (outcome.cancelled) PartStatus.CANCELLED else PartStatus.FAIL,
                kept, 0, out.size(partialUri), outcome.error ?: "cancelled by user", elapsed(),
            )
        }

        // FR-7: verify before the file gets its final name.
        val size = out.size(partialUri)
        val duration = try {
            MediaProbe.outputDurationUs(ctx, partialUri)
        } catch (e: Exception) {
            -1L
        }
        val reasons = mutableListOf<String>()
        val expected = part.expectedDurationUs
        val tolerance = maxOf(TOLERANCE_ABS_US, (TOLERANCE_REL * expected).toLong())
        if (duration < 0) {
            reasons.add("output cannot be read back")
        } else if (abs(duration - expected) > tolerance) {
            reasons.add("duration %.2fs vs expected %.2fs".format(duration / 1e6, expected / 1e6))
        }
        if (size < SIZE_MIN_RATIO * part.expectedSize) {
            reasons.add("size $size < 90% of ${part.expectedSize}")
        }
        if (reasons.isNotEmpty()) {
            val kept = keepIncomplete(out, partialUri, part)
            return PartResult(part, PartStatus.FAIL, kept, maxOf(duration, 0), size, reasons.joinToString("; "), elapsed())
        }

        out.rename(partialUri, part.outputName) // FR-6.4
        return PartResult(part, PartStatus.PASS, part.outputName, duration, size, "", elapsed())
    }

    /** A failed or cancelled output is kept under a warning name, never deleted (FR-6.5). */
    private fun keepIncomplete(out: OutputFolder, partialUri: Uri, part: OutputPart): String? = try {
        val stem = INCOMPLETE_PREFIX + part.outputName.removeSuffix(".mp4")
        val name = Planner.uniqueName(stem, ".mp4") { out.exists(it) }
        out.rename(partialUri, name)
        name
    } catch (_: Exception) {
        part.partialName
    }

    private fun copyAll(
        ctx: Context,
        part: OutputPart,
        target: Uri,
        cancel: AtomicBoolean,
        onBytes: (Long) -> Unit,
    ): CopyOutcome {
        val pfd = ctx.contentResolver.openFileDescriptor(target, "rw")
            ?: return CopyOutcome("cannot open output for writing", false)
        var muxer: MediaMuxer? = null
        var muxerStarted = false
        try {
            muxer = MediaMuxer(pfd.fileDescriptor, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            val buffer = ByteBuffer.allocateDirect(BUFFER_BYTES)
            val info = MediaCodec.BufferInfo()
            var outVideo = -1
            var outAudio = -1
            var offsetUs = 0L
            var lastAudioOut = -1L

            for (clip in part.clips) {
                if (cancel.get()) return CopyOutcome(null, true)
                val ex = MediaExtractor()
                try {
                    ex.setDataSource(ctx, Uri.parse(clip.uri), null)
                    val t = MediaProbe.selectTracks(ex)
                    if (t.video < 0) return CopyOutcome("${clip.name}: no video track", false)

                    if (!muxerStarted) {
                        val vf = ex.getTrackFormat(t.video)
                        outVideo = muxer.addTrack(vf)
                        if (t.audio >= 0) outAudio = muxer.addTrack(ex.getTrackFormat(t.audio))
                        if (vf.containsKey(MediaFormat.KEY_ROTATION)) {
                            muxer.setOrientationHint(vf.getInteger(MediaFormat.KEY_ROTATION))
                        }
                        muxer.start()
                        muxerStarted = true
                    }
                    ex.selectTrack(t.video)
                    val copyAudio = t.audio >= 0 && outAudio >= 0
                    if (copyAudio) ex.selectTrack(t.audio)

                    // Rebase the clip so its earliest sample lands on the running offset.
                    val base = maxOf(ex.sampleTime, 0L)
                    var clipEndUs = 0L
                    val lastPts = HashMap<Int, Long>()
                    val frameUs = HashMap<Int, Long>()

                    while (true) {
                        val track = ex.sampleTrackIndex
                        if (track < 0) break
                        if (cancel.get()) return CopyOutcome(null, true)
                        val size = ex.readSampleData(buffer, 0)
                        if (size < 0) break
                        val pts = ex.sampleTime
                        val outTrack = if (track == t.video) outVideo else outAudio

                        var outPts = offsetUs + (pts - base)
                        if (outTrack == outAudio && outPts <= lastAudioOut) outPts = lastAudioOut + 1
                        if (outTrack == outAudio) lastAudioOut = outPts

                        val keyFrame = ex.sampleFlags and MediaExtractor.SAMPLE_FLAG_SYNC != 0
                        info.set(0, size, outPts, if (keyFrame) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                        muxer.writeSampleData(outTrack, buffer, info)

                        lastPts[track]?.let { prev -> if (pts > prev) frameUs[track] = pts - prev }
                        lastPts[track] = pts
                        clipEndUs = maxOf(clipEndUs, pts - base + (frameUs[track] ?: 0L))
                        onBytes(size.toLong())
                        ex.advance()
                    }
                    offsetUs += clipEndUs
                } finally {
                    ex.release()
                }
            }
            if (!muxerStarted) return CopyOutcome("nothing to write", false)
            muxer.stop()
            return CopyOutcome(null, false)
        } catch (e: Exception) {
            return CopyOutcome(e.message ?: e.javaClass.simpleName, false)
        } finally {
            try {
                muxer?.release() // release() also stops a started muxer when we bailed out early
            } catch (_: Exception) {
            }
            pfd.close()
        }
    }
}
