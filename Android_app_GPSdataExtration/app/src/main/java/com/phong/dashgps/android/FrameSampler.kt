package com.phong.dashgps.android

import android.content.Context
import android.graphics.Bitmap
import android.media.Image
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import com.phong.dashgps.core.CropRect
import java.util.concurrent.atomic.AtomicBoolean

/** The luma (brightness) pixels of the overlay region of one frame. */
class LumaCrop(val width: Int, val height: Int, val data: ByteArray) {
    fun toBitmap(): Bitmap {
        val px = IntArray(width * height) { i ->
            val v = data[i].toInt() and 0xFF
            (0xFF shl 24) or (v shl 16) or (v shl 8) or v
        }
        return Bitmap.createBitmap(px, width, height, Bitmap.Config.ARGB_8888)
    }
}

class VideoInfo(val durationSec: Double, val width: Int, val height: Int, val mime: String)

/**
 * Pulls one frame every [intervalSec] seconds out of a video, as fast as possible.
 *
 * Only **keyframes** are decoded: for each target time the extractor seeks to the closest
 * sync sample, and only that sample is fed to the hardware decoder. Keyframes decode on
 * their own, so no other frames are needed. Vietmap records one keyframe per second and
 * its overlay also changes once per second, so this reads every overlay value while
 * decoding ~1/30 of the frames. If a camera's keyframes are further apart than the
 * interval, each keyframe is used once (sampling becomes the keyframe spacing).
 *
 * Only the Y (luma) plane of the [crop] region is copied out: the overlay text is white,
 * colour is not needed for OCR.
 */
object FrameSampler {
    private const val TIMEOUT_US = 10_000L

    fun info(ctx: Context, uri: Uri): VideoInfo {
        val ex = MediaExtractor()
        try {
            ex.setDataSource(ctx, uri, null)
            val track = videoTrack(ex)
            val f = ex.getTrackFormat(track)
            return VideoInfo(
                durationSec = if (f.containsKey(MediaFormat.KEY_DURATION)) f.getLong(MediaFormat.KEY_DURATION) / 1e6 else 0.0,
                width = f.getInteger(MediaFormat.KEY_WIDTH),
                height = f.getInteger(MediaFormat.KEY_HEIGHT),
                mime = f.getString(MediaFormat.KEY_MIME) ?: "",
            )
        } finally {
            ex.release()
        }
    }

    private fun videoTrack(ex: MediaExtractor): Int =
        (0 until ex.trackCount).firstOrNull {
            ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true
        } ?: throw IllegalArgumentException("no video track")

    /**
     * Calls [onFrame] (on this thread) with each sampled frame's position and overlay crop.
     * [onProgress] gets the current position in seconds.
     */
    fun sample(
        ctx: Context,
        uri: Uri,
        intervalSec: Double,
        crop: CropRect,
        cancel: AtomicBoolean,
        startSec: Double = 0.0,
        maxFrames: Int = Int.MAX_VALUE,
        onProgress: (Double) -> Unit = {},
        onFrame: (Double, LumaCrop) -> Unit,
    ) {
        val ex = MediaExtractor()
        var codec: MediaCodec? = null
        try {
            ex.setDataSource(ctx, uri, null)
            val track = videoTrack(ex)
            ex.selectTrack(track)
            val format = ex.getTrackFormat(track)
            val durationUs = if (format.containsKey(MediaFormat.KEY_DURATION)) format.getLong(MediaFormat.KEY_DURATION) else Long.MAX_VALUE
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)

            codec = MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME)!!)
            codec.configure(format, null, null, 0)
            codec.start()

            val intervalUs = (intervalSec * 1e6).toLong().coerceAtLeast(1)
            val info = MediaCodec.BufferInfo()
            var targetUs = (startSec * 1e6).toLong()
            var lastFedUs = -1L
            var fed = 0
            var inputDone = false

            while (!cancel.get()) {
                if (!inputDone) {
                    val inIndex = codec.dequeueInputBuffer(TIMEOUT_US)
                    if (inIndex >= 0) {
                        while (true) {
                            if (targetUs > durationUs || fed >= maxFrames) {
                                codec.queueInputBuffer(inIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                                inputDone = true
                                break
                            }
                            ex.seekTo(targetUs, MediaExtractor.SEEK_TO_CLOSEST_SYNC)
                            targetUs += intervalUs
                            val t = ex.sampleTime
                            if (t < 0 || t <= lastFedUs) continue // past the end, or the same keyframe again
                            val size = ex.readSampleData(codec.getInputBuffer(inIndex)!!, 0)
                            if (size < 0) continue
                            codec.queueInputBuffer(inIndex, 0, size, t, 0)
                            lastFedUs = t
                            fed++
                            onProgress(t / 1e6)
                            break
                        }
                    }
                }
                val outIndex = codec.dequeueOutputBuffer(info, TIMEOUT_US)
                if (outIndex >= 0) {
                    val eos = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    if (info.size > 0) {
                        codec.getOutputImage(outIndex)?.use { img ->
                            onFrame(info.presentationTimeUs / 1e6, cropLuma(img, crop))
                        }
                    }
                    codec.releaseOutputBuffer(outIndex, false)
                    if (eos) break
                }
            }
        } finally {
            try {
                codec?.stop()
            } catch (_: Exception) {
            }
            codec?.release()
            ex.release()
        }
    }

    fun cropLuma(img: Image, c: CropRect): LumaCrop {
        val visible = img.cropRect
        val w = visible.width()
        val h = visible.height()
        val x0 = visible.left + (c.left * w).toInt().coerceIn(0, w - 1)
        val x1 = visible.left + (c.right * w).toInt().coerceIn(1, w)
        val y0 = visible.top + (c.top * h).toInt().coerceIn(0, h - 1)
        val y1 = visible.top + (c.bottom * h).toInt().coerceIn(1, h)
        val cw = x1 - x0
        val ch = y1 - y0

        val plane = img.planes[0]
        val buf = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val out = ByteArray(cw * ch)
        for (y in 0 until ch) {
            val rowStart = (y0 + y) * rowStride + x0 * pixelStride
            if (pixelStride == 1) {
                buf.position(rowStart)
                buf.get(out, y * cw, cw)
            } else {
                for (x in 0 until cw) out[y * cw + x] = buf.get(rowStart + x * pixelStride)
            }
        }
        return LumaCrop(cw, ch, out)
    }
}
