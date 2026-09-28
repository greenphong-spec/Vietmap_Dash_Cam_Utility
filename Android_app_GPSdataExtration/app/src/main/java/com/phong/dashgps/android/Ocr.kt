package com.phong.dashgps.android

import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions

/**
 * On-device OCR (ML Kit text recognition, Latin model bundled in the APK - offline).
 * Recognition runs on ML Kit's own threads; callers keep a few requests in flight so the
 * decoder and the recognizer work in parallel.
 */
class Ocr : AutoCloseable {
    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    fun submit(crop: LumaCrop): Task<Text> = recognizer.process(InputImage.fromBitmap(crop.toBitmap(), 0))

    /** Blocks until [task] finishes; returns all recognised text on one line ("" on failure). */
    fun await(task: Task<Text>): String = try {
        Tasks.await(task).text.replace('\n', ' ')
    } catch (_: Exception) {
        ""
    }

    fun read(crop: LumaCrop): String = await(submit(crop))

    override fun close() = recognizer.close()
}
