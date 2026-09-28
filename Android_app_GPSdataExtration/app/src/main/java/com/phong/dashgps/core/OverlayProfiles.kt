package com.phong.dashgps.core

import java.time.DateTimeException
import java.time.LocalDateTime

/** Built-in camera overlay profiles. To support a new camera, add a profile here. */
object OverlayProfiles {

    /**
     * Vietmap SpeedMap M1, front camera (2560x1440). Bottom strip, left of centre:
     *   "Vietmap SpeedMap M1 13/09/2026 11:31:13 39KM/H N10.76736 E106.63187"
     * The rear camera only shows a date/time, no GPS.
     */
    val VIETMAP_M1_FRONT = OverlayProfile(
        id = "vietmap_m1_front",
        name = "Vietmap SpeedMap M1 – front camera",
        crop = CropRect(left = 0.0f, top = 0.90f, right = 0.80f, bottom = 1.0f),
        // Vietmap always prints exactly 5 decimals; OCR sometimes drops or adds one
        // ("106.63376" read as "106.633"), which would move the point ~80 m.
        parse = { parseDmyTimeSpeedLatLon(it, coordDecimals = 5) },
        note = "Use the Front video: the rear camera does not show GPS.",
    )

    val ALL = listOf(VIETMAP_M1_FRONT)

    fun byId(id: String?): OverlayProfile = ALL.firstOrNull { it.id == id } ?: ALL.first()

    // ---- Parsing helpers shared by profiles -------------------------------------------------

    /** Characters OCR commonly returns in place of digits, and the digit they stand for. */
    private val DIGIT_LOOKALIKES = mapOf(
        'O' to '0', 'Q' to '0', 'D' to '0', 'U' to '0',
        'I' to '1', 'L' to '1', '|' to '1', '!' to '1', 'T' to '1',
        'Z' to '2', 'S' to '5', 'B' to '8', 'G' to '6',
    )

    /** A "digit" as OCR may return it. */
    private const val D = "[0-9OQDUIL|!TZSBG]"

    /** dd/MM/yyyy; OCR may turn "/" into f, l, I or 1, or drop it. */
    private val DATE_DMY = Regex("""($D{2})\s*[/\\FLI1|]?\s*($D{2})\s*[/\\FLI1|]?\s*($D{4})""")
    private val TIME_HMS = Regex("""(?<![0-9])($D{1,2})\s*[:;.]\s*($D{2})\s*[:;.]\s*($D{2})(?![0-9])""")

    /** Looser time shape, only used to recover the seconds when a digit is lost ("07:1:00"). */
    private val TIME_LOOSE = Regex("""(?<![0-9])($D{1,2})\s*[:;.]\s*($D{1,2})\s*[:;.]\s*($D{2})(?![0-9])""")
    /** "39KM/H"; OCR also gives "6KNM/H", "20kKM/H", "35KMWH", "0KMIA": the number + KM is what counts. */
    private val SPEED_KMH = Regex("""($D{1,3})\s*K{1,2}\s*N?\s*M(?:\s*[/\\|IWFA1]{0,2}\s*H)?""")

    /**
     * Hemisphere letter, integer degrees, decimal point, decimals - not followed by another
     * digit. With a fixed decimal count the point may be misread (":", ",", ")", ...) or
     * missing, and a stray space may split the decimals: the last N digits are the decimals.
     */
    private fun coordRegex(hemispheres: String, intDigits: String, decimals: Int?): Regex {
        val body = if (decimals != null) {
            """($D{$intDigits})\s*[.,:;)(/]{0,2}\s*($D(?: ?$D){${decimals - 1}})"""
        } else {
            """($D{$intDigits})\s*[.,]\s*($D{3,7})"""
        }
        // No digit may touch the last decimal (that would be a 6th decimal); anything after a
        // space is ignored ("E 106.63704 !" is fine).
        return Regex("""([$hemispheres])\s*$body(?!$D)""")
    }

    private fun digits(s: String): Int? =
        s.filter { it != ' ' }.map { DIGIT_LOOKALIKES[it] ?: it }.joinToString("").toIntOrNull()

    private fun decimal(intPart: String, frac: String): Double? {
        val i = digits(intPart) ?: return null
        val f = frac.filter { it != ' ' }.map { DIGIT_LOOKALIKES[it] ?: it }.joinToString("")
        if (!f.all { it.isDigit() }) return null
        return "$i.$f".toDoubleOrNull()
    }

    /** Uppercase, one line, single spaces (ML Kit returns one line per text block). */
    fun normalize(text: String): String = text.uppercase().replace(Regex("""\s+"""), " ").trim()

    private fun overlaps(a: IntRange, others: List<IntRange>) = others.any { a.first <= it.last && it.first <= a.last }

    /**
     * "dd/MM/yyyy HH:mm:ss <speed>KM/H N<lat> E<lon>" in any order, tolerant of the usual OCR
     * confusions (O/0, l/1, S/5, extra or missing spaces). [coordDecimals] = exact number of
     * decimals the camera prints (null = accept 3..7 with a proper decimal point).
     *
     * Position and speed are required. If the date or time cannot be read, the reading is
     * still returned with time = null and the seconds that could be read in [Reading.secondHint],
     * so TrackCleaner can rebuild the time from the video clock; with no seconds either,
     * the text is unusable (null).
     */
    fun parseDmyTimeSpeedLatLon(text: String, coordDecimals: Int? = null): Reading? {
        val t = normalize(text)
        // The coordinates are matched first so their digits are not mistaken for the date/time.
        val lat = coordRegex("NS", "1,2", coordDecimals).find(t) ?: return null
        val lonRegex = coordRegex("EW", "1,3", coordDecimals)
        val lon = lonRegex.find(t, lat.range.last + 1) ?: lonRegex.find(t) ?: return null
        val coords = listOf(lat.range, lon.range)
        val speed = SPEED_KMH.findAll(t).firstOrNull { !overlaps(it.range, coords) } ?: return null
        val taken = coords + listOf(speed.range)
        val date = DATE_DMY.findAll(t).firstOrNull { !overlaps(it.range, taken) }
        val time = TIME_HMS.findAll(t).firstOrNull { !overlaps(it.range, taken + listOfNotNull(date?.range)) }

        val latAbs = decimal(lat.groupValues[2], lat.groupValues[3]) ?: return null
        val lonAbs = decimal(lon.groupValues[2], lon.groupValues[3]) ?: return null
        val stamp = if (date != null && time != null) {
            try {
                LocalDateTime.of(
                    digits(date.groupValues[3]) ?: 0,
                    digits(date.groupValues[2]) ?: 0,
                    digits(date.groupValues[1]) ?: 0,
                    digits(time.groupValues[1]) ?: -1,
                    digits(time.groupValues[2]) ?: -1,
                    digits(time.groupValues[3]) ?: -1,
                )
            } catch (_: DateTimeException) {
                null
            }
        } else {
            null
        }
        val secondHint = if (stamp != null) {
            stamp.second
        } else {
            (time ?: TIME_LOOSE.findAll(t).firstOrNull { !overlaps(it.range, taken) })
                ?.let { digits(it.groupValues[3]) }?.takeIf { it in 0..59 } ?: return null
        }
        return Reading(
            time = stamp,
            lat = if (lat.groupValues[1] == "S") -latAbs else latAbs,
            lon = if (lon.groupValues[1] == "W") -lonAbs else lonAbs,
            speedKmh = digits(speed.groupValues[1]) ?: return null,
            secondHint = secondHint,
        )
    }
}
