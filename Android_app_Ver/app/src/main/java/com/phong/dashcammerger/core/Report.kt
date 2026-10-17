package com.phong.dashcammerger.core

import java.time.format.DateTimeFormatter
import java.util.Locale

/** Human-readable plan summary and run report (port of core/report.py) - FR-9. */
object Report {
    const val REPORT_FILENAME = "merge_report.txt"

    private const val KIB = 1024.0
    private const val MIB = 1024.0 * 1024.0
    private const val GIB = 1024.0 * 1024.0 * 1024.0

    /** H:MM:SS */
    fun formatDuration(us: Long): String {
        val total = Math.round(maxOf(0L, us) / 1e6)
        return String.format(Locale.US, "%d:%02d:%02d", total / 3600, (total % 3600) / 60, total % 60)
    }

    /** GB with 2 decimals, MB with 1, otherwise KB. */
    fun formatSize(bytes: Long): String = when {
        bytes >= GIB -> String.format(Locale.US, "%.2f GB", bytes / GIB)
        bytes >= MIB -> String.format(Locale.US, "%.1f MB", bytes / MIB)
        else -> String.format(Locale.US, "%.1f KB", bytes / KIB)
    }

    private val DAY_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    private val TIME = DateTimeFormatter.ofPattern("HH:mm:ss")

    /** "trip 2/9", "trip 2/9 part 1/2", "part 1/2" or "" for a single file (FR-3.5). */
    fun partLabel(part: OutputPart): String {
        var label = if (part.tripCount > 1) "trip ${part.tripIndex}/${part.tripCount}" else ""
        if (part.tripParts > 1) label = "$label part ${part.tripPart}/${part.tripParts}".trim()
        return label
    }

    /** "2026-10-14 15:42:40 - 16:12:39"; the end keeps its date only on another day; "" if unknown. */
    fun partSpan(part: OutputPart): String {
        val start = Arrange.start(part) ?: return ""
        val end = Arrange.end(part) ?: return ""
        val endText = if (end.toLocalDate() == start.toLocalDate()) end.format(TIME) else end.format(DAY_TIME)
        return "${start.format(DAY_TIME)} - $endText"
    }

    /** Live line under the selection list (AR-10.8). */
    fun selectionSummary(selected: List<OutputPart>, total: Int): String =
        "Selected: ${selected.size} of $total file(s), ${formatSize(selected.sumOf { it.expectedSize })}"

    /** AR-10.3 / AR-10.9: empty unless the output is known not to fit. */
    fun spaceWarning(needed: Long, free: Long?): String =
        if (free != null && needed > free) {
            "NOT ENOUGH SPACE: needs about ${formatSize(needed)}, only ${formatSize(free)} free " +
                "in the output folder. Free up space or choose another output folder."
        } else {
            ""
        }

    /** Confirmation text shown before anything is written (FR-2.4). */
    fun planSummary(plan: MergePlan, outputFolder: String): String = buildString {
        for (group in plan.groups) {
            val codec = group.clips.firstNotNullOfOrNull { it.probe?.description }?.let { "  [$it]" } ?: ""
            appendLine("${group.camera}: ${group.clips.size} files, ${formatSize(group.clips.sumOf { it.sizeBytes })}$codec")
        }
        appendLine("Unclassified: ${plan.unclassified.size}")
        appendLine("Unreadable: ${plan.unreadable.size}")
        appendLine("Parts to write: ${plan.parts.size}")
        for (part in plan.parts) {
            val label = partLabel(part).let { if (it.isEmpty()) "" else " $it" }
            val span = partSpan(part).let { if (it.isEmpty()) "" else "$it, " }
            appendLine("  ${part.camera}$label: $span${part.clips.size} clips, ${formatDuration(part.expectedDurationUs)}, ${formatSize(part.expectedSize)}")
            appendLine("    ${part.outputName}")
        }
        appendLine("Output folder: $outputFolder")
        if (plan.warnings.isNotEmpty()) {
            appendLine("Warnings:")
            plan.warnings.forEach { appendLine("  $it") }
        }
    }

    /** The report written to merged/merge_report.txt and shown on screen. */
    fun reportText(report: RunReport): String = buildString {
        val plan = report.plan
        for (r in report.results) {
            val p = r.part
            appendLine("${p.camera} ${partLabel(p).ifEmpty { "part ${p.partIndex}" }}: ${r.finalName ?: p.outputName}")
            appendLine(
                "    ${p.clips.size} files, ${formatDuration(if (r.actualDurationUs > 0) r.actualDurationUs else p.expectedDurationUs)}, " +
                    "${formatSize(r.actualSize)}  ->  ${r.status}" + if (r.reason.isNotEmpty()) " (${r.reason})" else ""
            )
        }
        if (report.skipped.isNotEmpty()) {
            appendLine("Not selected (skipped by user): ${report.skipped.size}")
            report.skipped.forEach { appendLine("    ${it.outputName}") }
        }
        appendLine(
            "Unclassified: ${plan.unclassified.size}   Skipped unreadable: ${plan.unreadable.size}   " +
                "Sources untouched: ${if (report.sourcesUntouched) "OK" else "CHANGED"}"
        )
        appendLine("Output folder: ${report.outputFolder}")
        if (report.sourceProblems.isNotEmpty()) {
            appendLine()
            appendLine("Source problems:")
            report.sourceProblems.forEach { appendLine("  $it") }
        }
        if (plan.warnings.isNotEmpty()) {
            appendLine()
            appendLine("Warnings:")
            plan.warnings.forEach { appendLine("  $it") }
        }
    }
}
