package com.phong.dashgps.android

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.DocumentsContract.Document
import android.provider.OpenableColumns

class VideoItem(val uri: Uri, val name: String, val sizeBytes: Long) {
    val stem: String get() = name.substringBeforeLast('.', name)
}

object Storage {
    fun videoItem(ctx: Context, uri: Uri): VideoItem {
        var name = uri.lastPathSegment ?: "video"
        var size = 0L
        ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use {
            if (it.moveToFirst()) {
                it.getString(0)?.let { n -> name = n }
                if (!it.isNull(1)) size = it.getLong(1)
            }
        }
        return VideoItem(uri, name, size)
    }

    /** "Phone: /Movies/GPS" or "SD card/USB (1721-8D53): /DCIM" from a picked folder. */
    fun describeTree(treeUri: Uri): String = try {
        val id = DocumentsContract.getTreeDocumentId(treeUri)
        val (volume, path) = id.split(':', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        (if (volume == "primary") "Phone" else "SD card/USB ($volume)") + ": /$path"
    } catch (_: Exception) {
        treeUri.toString()
    }
}

/**
 * Writes export files into one user-picked folder. It only ever creates new files - an
 * existing name gets _2, _3, ... - and has no delete or overwrite capability.
 */
class ExportFolder(private val ctx: Context, private val treeUri: Uri) {
    private val dirId = DocumentsContract.getTreeDocumentId(treeUri)
    private val dirUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, dirId)

    private fun existingNames(): Set<String> {
        val names = HashSet<String>()
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, dirId)
        ctx.contentResolver.query(children, arrayOf(Document.COLUMN_DISPLAY_NAME), null, null, null)?.use {
            while (it.moveToNext()) it.getString(0)?.let { n -> names.add(n.lowercase()) }
        }
        return names
    }

    fun uniqueName(stem: String, ext: String): String {
        val taken = existingNames()
        var name = "$stem.$ext"
        var n = 2
        while (name.lowercase() in taken) name = "${stem}_${n++}.$ext"
        return name
    }

    /** Creates [name] and lets [write] fill it. Returns the new document's URI. */
    fun create(name: String, mime: String, write: (java.io.OutputStream) -> Unit): Uri {
        var uri = DocumentsContract.createDocument(ctx.contentResolver, dirUri, mime, name)
            ?: throw IllegalStateException("Cannot create $name")
        // Some providers append an extension they derive from the MIME type; put the name back.
        val actual = ctx.contentResolver.query(uri, arrayOf(Document.COLUMN_DISPLAY_NAME), null, null, null)
            ?.use { if (it.moveToFirst()) it.getString(0) else null }
        if (actual != null && actual != name) {
            uri = DocumentsContract.renameDocument(ctx.contentResolver, uri, name) ?: uri
        }
        ctx.contentResolver.openOutputStream(uri, "w")!!.use(write)
        return uri
    }
}
