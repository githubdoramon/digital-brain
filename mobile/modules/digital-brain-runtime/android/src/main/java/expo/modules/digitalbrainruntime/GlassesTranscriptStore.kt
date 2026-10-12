package expo.modules.digitalbrainruntime

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import com.google.android.gms.auth.api.signin.GoogleSignIn
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId

/** User text is deliberately separate from sanitized diagnostic exports. No audio is persisted. */
internal object GlassesTranscriptStore {
  fun account(c: Context): String? = GoogleSignIn.getLastSignedInAccount(c)?.email?.let {
    MessageDigest.getInstance("SHA-256").digest(it.lowercase().toByteArray()).joinToString("") { b -> "%02x".format(b) }
  }
  private fun folder(c: Context, account: String) = File(c.filesDir, "glasses-transcripts/$account").apply { mkdirs() }
  fun prefs(c: Context, account: String) = c.getSharedPreferences("glasses_listening_$account", Context.MODE_PRIVATE)
  private fun repairTail(file: File) {
    if (!file.exists() || file.length() == 0L) return
    java.io.RandomAccessFile(file, "rw").use { data ->
      data.seek(data.length() - 1)
      if (data.read() == 10) return
      val start = maxOf(0L, data.length() - 65536)
      val tail = ByteArray((data.length() - start).toInt())
      data.seek(start); data.readFully(tail)
      val newline = tail.indexOfLast { it == 10.toByte() }
      check(newline >= 0 || start == 0L) { "Transcript file needs recovery" }
      data.setLength(if (newline >= 0) start + newline + 1 else 0)
    }
  }
  @Synchronized fun append(c: Context, account: String, chunk: SpeechChunk, text: String, inferenceMs: Long) {
    val dir = folder(c, account); check(dir.usableSpace > 16 * 1024 * 1024)
    val start = Instant.ofEpochMilli(chunk.startedAtMs)
    val duration = chunk.pcm.size * 1000L / 32000
    val entry = JSONObject().put("startedAt", start.toString()).put("endedAt", start.plusMillis(duration).toString())
      .put("timezone", ZoneId.systemDefault().id).put("language", "en").put("text", text)
      .put("speechDurationMs", duration).put("transcriptionDurationMs", inferenceMs)
    val name = start.toString().take(10) + ".jsonl"
    val file = File(dir, name); repairTail(file)
    FileOutputStream(file, true).use { out -> out.write((entry.toString() + "\n").toByteArray()); out.fd.sync() }
    val pref = prefs(c, account)
    pref.edit().putLong("count", pref.getLong("count", 0) + 1).putLong("lastTranscriptAtMs", chunk.startedAtMs).commit()
  }
  @Synchronized fun export(c: Context, account: String, baseUri: String): String {
    val files = folder(c, account).listFiles()?.filter { it.extension == "jsonl" }?.sortedBy { it.name } ?: emptyList()
    files.forEach(::repairTail)
    check(files.isNotEmpty()) { "No transcripts to export yet" }
    val base = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(baseUri)))
    check(base.canWrite()) { "Choose a writable Digital Brain folder" }
    val exports = base.findFile("Exports") ?: base.createDirectory("Exports")
    check(exports?.isDirectory == true)
    val name = "glasses-transcripts-${System.currentTimeMillis()}.jsonl"
    val target = checkNotNull(exports.createFile("application/x-ndjson", name))
    try {
      var total = 0L
      checkNotNull(c.contentResolver.openOutputStream(target.uri, "wt")).use { out ->
        files.forEach { file -> file.inputStream().use { total += it.copyTo(out) } }; out.flush()
      }
      check(total > 0 && target.length() == total) { "Could not finish transcript export" }
      return target.uri.toString()
    } catch (e: Exception) { target.delete(); throw e }
  }
}
