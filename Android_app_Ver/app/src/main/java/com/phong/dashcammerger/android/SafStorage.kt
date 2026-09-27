package com.phong.dashcammerger.android

import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.StatFs
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import com.phong.dashcammerger.core.ClipInfo
import com.phong.dashcammerger.core.INCOMPLETE_PREFIX
import java.io.File

/** Storage Access Framework helpers: scanning the picked folder (port of core/scanner.py) - FR-1. */
object SafScanner {
    /** .avi is left out: Android's MediaExtractor cannot read it. */
    val VIDEO_EXTENSIONS = setOf("mp4", "mov", "ts", "mkv", "3gp")

    private val CHILD_COLUMNS = arrayOf(
        Document.COLUMN_DOCUMENT_ID,
        Document.COLUMN_DISPLAY_NAME,
        Document.COLUMN_MIME_TYPE,
        Document.COLUMN_SIZE,
        Document.COLUMN_LAST_MODIFIED,
    )

    /** Human-readable location of a picked tree, e.g. "SD card (1234-ABCD): /DCIM/Movie". */
    fun describeTree(ctx: Context, treeUri: Uri): String = try {
        val id = DocumentsContract.getTreeDocumentId(treeUri)
        val (volume, path) = id.split(':', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        val where = if (volume == "primary") "Phone" else "SD card/USB ($volume)"
        "$where: /$path"
    } catch (_: Exception) {
        displayName(ctx, treeUri) ?: treeUri.toString()
    }

    fun displayName(ctx: Context, docUri: Uri): String? =
        ctx.contentResolver.query(docUri, arrayOf(Document.COLUMN_DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        }

    /** Size and mtime of one document, or null if it is gone. */
    fun stat(ctx: Context, docUri: Uri): Pair<Long, Long>? = try {
        ctx.contentResolver.query(
            docUri, arrayOf(Document.COLUMN_SIZE, Document.COLUMN_LAST_MODIFIED), null, null, null
        )?.use { if (it.moveToFirst()) it.getLong(0) to it.getLong(1) else null }
    } catch (_: Exception) {
        null
    }

    /**
     * Every source clip under the picked tree. Skips the tool's own output folder
     * (FR-1.3) - by name, and by document ID when a separate output folder lies
     * inside the input tree - and previously failed outputs (FR-1.4).
     */
    fun scan(
        ctx: Context,
        treeUri: Uri,
        outputSubfolder: String,
        skipDocIds: Set<String> = emptySet(),
        recursive: Boolean = true,
    ): List<ClipInfo> {
        val rootId = DocumentsContract.getTreeDocumentId(treeUri)
        val rootName = displayName(ctx, DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId)) ?: ""
        val clips = mutableListOf<ClipInfo>()
        val pending = ArrayDeque<Pair<String, String>>() // documentId, folder name
        pending.add(rootId to rootName)

        while (pending.isNotEmpty()) {
            val (dirId, dirName) = pending.removeFirst()
            val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, dirId)
            ctx.contentResolver.query(childrenUri, CHILD_COLUMNS, null, null, null)?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getString(0)
                    val name = c.getString(1) ?: continue
                    val mime = c.getString(2) ?: ""
                    if (mime == Document.MIME_TYPE_DIR) {
                        if (recursive && !name.equals(outputSubfolder, ignoreCase = true) && id !in skipDocIds) {
                            pending.add(id to name)
                        }
                        continue
                    }
                    if (name.startsWith(INCOMPLETE_PREFIX)) continue
                    if (name.substringAfterLast('.', "").lowercase() !in VIDEO_EXTENSIONS) continue
                    clips.add(
                        ClipInfo(
                            uri = DocumentsContract.buildDocumentUriUsingTree(treeUri, id).toString(),
                            name = name,
                            parentName = dirName,
                            sizeBytes = c.getLong(3),
                            mtimeMs = c.getLong(4),
                        )
                    )
                }
            }
        }
        clips.sortBy { it.uri.lowercase() } // deterministic; real order comes later
        return clips
    }
}

class SafetyViolation(message: String) : Exception(message)

/**
 * The only class allowed to create, write or rename files (port of safe_io.WriteGuard, FR-8).
 *
 * Everything it creates lives directly inside the one output folder, and it will only
 * rename documents it created itself in this run. There is deliberately no delete method.
 */
class OutputFolder private constructor(
    private val ctx: Context,
    private val treeUri: Uri,
    val dirUri: Uri,
    val displayPath: String,
) {
    private val created = mutableSetOf<Uri>()

    companion object {
        /**
         * The run's output folder: the user-picked [outputTree] itself, or
         * `<input folder>/<subfolder>` when none was picked (or it is the input folder).
         */
        fun open(ctx: Context, inputTree: Uri, outputTree: Uri?, subfolder: String): OutputFolder {
            val inputId = DocumentsContract.getTreeDocumentId(inputTree)
            if (outputTree != null) {
                val outId = DocumentsContract.getTreeDocumentId(outputTree)
                if (outId != inputId) {
                    val dir = DocumentsContract.buildDocumentUriUsingTree(outputTree, outId)
                    return OutputFolder(ctx, outputTree, dir, SafScanner.describeTree(ctx, outputTree))
                }
            }
            val rootUri = DocumentsContract.buildDocumentUriUsingTree(inputTree, inputId)
            val existing = findChild(ctx, inputTree, inputId, subfolder)
            val dir = existing?.takeIf { it.second == Document.MIME_TYPE_DIR }?.first
                ?: DocumentsContract.createDocument(ctx.contentResolver, rootUri, Document.MIME_TYPE_DIR, subfolder)
                ?: throw SafetyViolation("Cannot create output folder '$subfolder'")
            return OutputFolder(ctx, inputTree, dir, SafScanner.describeTree(ctx, inputTree).trimEnd('/') + "/$subfolder")
        }

        private fun findChild(ctx: Context, treeUri: Uri, parentId: String, name: String): Pair<Uri, String>? {
            val children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentId)
            ctx.contentResolver.query(
                children,
                arrayOf(Document.COLUMN_DOCUMENT_ID, Document.COLUMN_DISPLAY_NAME, Document.COLUMN_MIME_TYPE),
                null, null, null,
            )?.use { c ->
                while (c.moveToNext()) {
                    if (c.getString(1).equals(name, ignoreCase = true)) {
                        return DocumentsContract.buildDocumentUriUsingTree(treeUri, c.getString(0)) to (c.getString(2) ?: "")
                    }
                }
            }
            return null
        }
    }

    /** Document ID of the output folder; the scanner skips it so outputs are never re-merged. */
    val dirId: String get() = DocumentsContract.getDocumentId(dirUri)

    fun find(name: String): Uri? = findChild(ctx, treeUri, dirId, name)?.first

    fun exists(name: String): Boolean = find(name) != null

    /** Create a new, empty file with exactly this name (refuses to reuse an existing one). */
    fun createFile(name: String, mime: String): Uri {
        if (name.contains('/')) throw SafetyViolation("Refusing to write outside output folder: $name")
        if (exists(name)) throw SafetyViolation("Refusing to overwrite existing file: $name")
        val uri = DocumentsContract.createDocument(ctx.contentResolver, dirUri, mime, name)
            ?: throw SafetyViolation("Cannot create $name")
        created.add(uri)
        val actual = SafScanner.displayName(ctx, uri)
        if (actual != null && actual != name) throw SafetyViolation("Storage renamed $name to $actual")
        return uri
    }

    fun rename(uri: Uri, newName: String): Uri {
        if (uri !in created) throw SafetyViolation("Refusing to rename a file this run did not create: $uri")
        if (newName.contains('/')) throw SafetyViolation("Refusing to write outside output folder: $newName")
        val renamed = DocumentsContract.renameDocument(ctx.contentResolver, uri, newName)
            ?: throw SafetyViolation("Cannot rename to $newName")
        created.add(renamed)
        return renamed
    }

    /** Write UTF-8 text with \n newlines (FR-5.3); replaces an earlier file of the same name. */
    fun writeText(name: String, text: String): Uri {
        val uri = find(name)?.also { created.add(it) } ?: createFile(name, "text/plain")
        ctx.contentResolver.openOutputStream(uri, "wt")!!.use { it.write(text.toByteArray(Charsets.UTF_8)) }
        return uri
    }

    fun size(uri: Uri): Long = SafScanner.stat(ctx, uri)?.first ?: 0L

    /** Free space on the output volume, or null when the provider does not report it. */
    fun availableBytes(): Long? = try {
        // The provider's roots table needs a system permission, so stat the volume instead.
        // External-storage document IDs look like "primary:Movies" or "1721-8D53:DCIM".
        if (dirUri.authority != "com.android.externalstorage.documents") {
            null
        } else {
            val volume = dirId.substringBefore(':')
            val path = if (volume == "primary") Environment.getExternalStorageDirectory() else File("/storage/$volume")
            StatFs(path.path).availableBytes
        }
    } catch (_: Exception) {
        null
    }
}
