package com.phong.dashgps.core

import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration

/**
 * Replays the OCR text of every frame of a real Vietmap M1 front video (23 min, 1387
 * frames, recorded by the app on the phone as its "OCR log") through parser + cleaner.
 * Guards the real-world read rate and the absence of holes and jumps in the track.
 */
class RealVideoReplayTest {
    private fun loadSamples(): List<Sample> {
        val lines = javaClass.getResourceAsStream("/vietmap_m1_front_ocr_log.csv")!!
            .bufferedReader(Charsets.UTF_8).readLines().drop(1)
        return lines.map { line ->
            val f = splitCsv(line)
            Sample(0, f[0], f[1].toDouble(), f[3])
        }
    }

    /** Minimal CSV split: comma-separated, double quotes around fields, "" = quote. */
    private fun splitCsv(line: String): List<String> {
        val out = mutableListOf<String>()
        val cur = StringBuilder()
        var quoted = false
        var i = 0
        while (i < line.length) {
            val c = line[i]
            when {
                quoted && c == '"' && line.getOrNull(i + 1) == '"' -> { cur.append('"'); i++ }
                c == '"' -> quoted = !quoted
                c == ',' && !quoted -> { out.add(cur.toString()); cur.clear() }
                else -> cur.append(c)
            }
            i++
        }
        out.add(cur.toString())
        return out
    }

    @Test
    fun realVideoReadsCleanly() {
        val samples = loadSamples()
        val track = TrackCleaner.clean(samples, OverlayProfiles.VIETMAP_M1_FRONT)
        val kept = track.fixes.size
        val longestHole = track.fixes.zipWithNext().maxOf { (a, b) -> Duration.between(a.time, b.time).seconds }
        val worstJumpKmh = track.fixes.zipWithNext().maxOf { (a, b) ->
            Geo.distanceM(a, b) / Duration.between(a.time, b.time).seconds.coerceAtLeast(1) * 3.6
        }
        println(
            "replay: samples=${samples.size} kept=$kept repaired=${track.timeRepaired} " +
                "speedCorrected=${track.speedCorrected} maxSpeed=${track.stats.maxSpeedKmh}km/h " +
                "rejected=${track.rejected} longestHole=${longestHole}s worstStep=${worstJumpKmh.toInt()}km/h " +
                "distance=${"%.2f".format(track.stats.distanceM / 1000)}km"
        )
        // First version: 1214 kept, a 60 s hole in minute 07:11, 80 m jumps at 0 km/h.
        // Now 1308 kept (94%); the remaining holes are frames whose text is truly garbled
        // (bright background behind the overlay around 07:29-07:30).
        assertTrue("kept $kept", kept >= 1300)
        assertTrue("repaired ${track.timeRepaired}", track.timeRepaired >= 40)
        assertTrue("longest hole $longestHole s", longestHole <= 20)
        assertTrue("worst step $worstJumpKmh km/h", worstJumpKmh <= 80)
    }
}
