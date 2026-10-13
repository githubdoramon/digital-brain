package expo.modules.digitalbrainruntime

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import com.google.android.gms.auth.api.signin.GoogleSignIn
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import java.util.UUID

/** Explicit wake captures only. Private recovery and user-selected storage never enter diagnostics. */
internal object GlassesWakeStore {
  private val processing = mutableSetOf<String>()
  @Synchronized fun endProcessing(dir: File) { processing.remove(dir.path) }
  fun account(c: Context): String? = GoogleSignIn.getLastSignedInAccount(c)?.email?.let {
    MessageDigest.getInstance("SHA-256").digest(it.lowercase().toByteArray()).joinToString("") { b -> "%02x".format(b) }
  }
  fun prefs(c: Context, account: String) = c.getSharedPreferences("glasses_listening_$account", Context.MODE_PRIVATE)
  private fun pending(c: Context, account: String) = File(c.filesDir, "glasses-wake-pending/$account").apply { mkdirs() }
  fun pendingCount(c: Context, account: String): Int = pending(c, account).listFiles()?.count { File(it, "capture.json").isFile } ?: 0
  @Synchronized fun deleteLegacy(c: Context) {
    val legacy = File(c.filesDir, "glasses-transcripts")
    check(!legacy.exists() || legacy.deleteRecursively()) { "Could not remove old transcript logs" }
  }
  fun validateBase(c: Context, uri: String) {
    val base = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(uri)))
    check(base.isDirectory && base.canWrite()) { "Choose a writable Digital Brain folder" }
  }
  private fun atomic(file: File, data: ByteArray) {
    val temp = File(file.parentFile, file.name + ".tmp")
    FileOutputStream(temp).use { it.write(data); it.fd.sync() }
    check(temp.renameTo(file)) { "Could not save capture on this phone" }
  }
  fun wav(pcm: ByteArray): ByteArray {
    val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
    header.put("RIFF".toByteArray()).putInt(36 + pcm.size).put("WAVEfmt ".toByteArray())
      .putInt(16).putShort(1).putShort(1).putInt(16000).putInt(32000).putShort(2).putShort(16)
      .put("data".toByteArray()).putInt(pcm.size)
    return header.array() + pcm
  }
  @Synchronized fun stage(c: Context, account: String, capture: WakeCapture, baseUri: String): File {
    val root = pending(c, account)
    check(root.usableSpace > 16 * 1024 * 1024) { "Free some phone storage to save captures" }
    val name = "wake-${Instant.ofEpochMilli(capture.wakeAtMs).toString().replace(':', '-')}-${UUID.randomUUID()}"
    val dir = File(root, name).apply { check(mkdir()) }
    try {
      atomic(File(dir, "audio.wav"), wav(capture.chunk.pcm))
      val meta = JSONObject().put("schemaVersion", 1).put("startedAt", Instant.ofEpochMilli(capture.chunk.startedAtMs))
        .put("wakeDetectedAt", Instant.ofEpochMilli(capture.wakeAtMs)).put("endedAt", Instant.ofEpochMilli(capture.endAtMs))
        .put("timezone", ZoneId.systemDefault().id).put("language", "en").put("text", "")
        .put("receivedAudioDurationMs", capture.chunk.pcm.size * 1000L / 32000)
        .put("endReason", capture.reason.name.lowercase()).put("transcriptionStatus", "pending")
        .put("baseUri", baseUri)
      atomic(File(dir, "capture.json"), meta.toString().toByteArray())
      processing.add(dir.path)
      return dir
    } catch (e: Exception) { dir.deleteRecursively(); throw e }
  }
  @Synchronized fun transcript(dir: File, text: String, duration: Long, success: Boolean) {
    val meta = JSONObject(File(dir, "capture.json").readText()).put("text", text)
      .put("transcriptionDurationMs", duration).put("transcriptionStatus", if (success) "complete" else "failed")
    atomic(File(dir, "capture.json"), meta.toString().toByteArray())
  }
  @Synchronized fun publish(c: Context, account: String, dir: File, overrideBase: String? = null) {
    val journal = File(dir, "capture.json")
    val meta = JSONObject(journal.readText())
    val baseUri = overrideBase ?: meta.getString("baseUri")
    if (baseUri != meta.getString("baseUri")) { meta.remove("wavUri"); meta.remove("jsonUri") }
    meta.put("baseUri", baseUri)
    if (meta.getString("transcriptionStatus") == "pending") meta.put("transcriptionStatus", "interrupted")
    validateBase(c, baseUri)
    val base = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(baseUri)))
    val folder = base.findFile("Wake captures") ?: checkNotNull(base.createDirectory("Wake captures"))
    check(folder.isDirectory && folder.canWrite())
    fun target(key: String, mime: String, extension: String): DocumentFile {
      val existing = meta.optString(key).takeIf { it.isNotEmpty() }?.let { DocumentFile.fromSingleUri(c, Uri.parse(it)) }
      if (existing?.exists() == true) return existing
      val created = checkNotNull(folder.createFile(mime, dir.name + extension))
      meta.put(key, created.uri.toString()); atomic(journal, meta.toString().toByteArray())
      return created
    }
    val audio = File(dir, "audio.wav")
    val wav = target("wavUri", "audio/wav", ".wav")
    checkNotNull(c.contentResolver.openOutputStream(wav.uri, "wt")).use { out -> audio.inputStream().use { it.copyTo(out) } }
    check(wav.length() == audio.length()) { "Could not finish saving wake audio" }
    fun digest(input: java.io.InputStream): ByteArray = input.use { stream ->
      val hash = MessageDigest.getInstance("SHA-256")
      val buffer = ByteArray(8192)
      while (true) { val count = stream.read(buffer); if (count < 0) break; if (count > 0) hash.update(buffer, 0, count) }
      hash.digest()
    }
    check(digest(audio.inputStream()).contentEquals(digest(checkNotNull(c.contentResolver.openInputStream(wav.uri))))) {
      "Could not verify saved wake audio"
    }
    val json = target("jsonUri", "application/json", ".json")
    val publicMeta = JSONObject(meta.toString()).apply { remove("baseUri"); remove("wavUri"); remove("jsonUri"); put("audioFile", wav.name) }
    val bytes = publicMeta.toString(2).toByteArray()
    checkNotNull(c.contentResolver.openOutputStream(json.uri, "wt")).use { it.write(bytes) }
    check(json.length() == bytes.size.toLong()) { "Could not finish saving wake transcription" }
    check(MessageDigest.getInstance("SHA-256").digest(bytes).contentEquals(digest(checkNotNull(c.contentResolver.openInputStream(json.uri))))) {
      "Could not verify saved wake transcription"
    }
    val pref = prefs(c, account)
    val receipts = pref.getStringSet("wakeSavedReceipts", emptySet())!!.toMutableSet()
    if (dir.name !in receipts) {
      receipts.add(dir.name)
      check(pref.edit().putLong("wakeSavedCount", pref.getLong("wakeSavedCount", 0) + 1)
        .putLong("wakeLastSavedAtMs", Instant.parse(meta.getString("wakeDetectedAt")).toEpochMilli())
        .putStringSet("wakeSavedReceipts", receipts.toList().takeLast(32).toSet()).commit())
    }
    check(dir.deleteRecursively()) { "Capture saved; phone cleanup needs retry" }
  }
  @Synchronized fun retry(c: Context, account: String, baseUri: String) {
    pending(c, account).listFiles()?.filter { File(it, "capture.json").isFile }?.sortedBy { it.name }
      ?.filter { it.path !in processing }?.forEach { publish(c, account, it, baseUri) }
  }
}
