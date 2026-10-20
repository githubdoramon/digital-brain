package expo.modules.digitalbrainruntime

import android.content.Context
import androidx.documentfile.provider.DocumentFile
import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

internal enum class SpeechEvalRunState(val key: String) { EMPTY("empty"), READY("ready"), RUNNING("running"), PAUSED("paused"), COMPLETE("complete") }
internal enum class SpeechEvalResult(val key: String) { COMPLETE("complete"), FAILED("failed") }
internal enum class SpeechEvalMode(val key: String) { COLD("cold"), WARM("warm") }
internal fun JSONObject.runState() = SpeechEvalRunState.entries.firstOrNull { it.key == optString("state") } ?: SpeechEvalRunState.EMPTY

/** Private account-scoped experiment journal; originals are never edited. */
internal object SpeechEvalStore {
  fun owner(c: Context) = checkNotNull(GlassesWakeStore.account(c)) { "Sign in first" }
  fun root(c: Context, owner: String) = File(c.filesDir, "speech-eval/$owner").apply { mkdirs() }
  fun run(c: Context, owner: String) = File(root(c, owner), "run.json")
  fun readMeta(c: Context, owner: String): JSONObject? = run(c, owner).takeIf { it.exists() }?.let { JSONObject(it.readText()) }
  fun read(c: Context, owner: String): JSONObject? = readMeta(c, owner)?.also { meta ->
    val rows = File(root(c, owner), "${meta.getString("id")}/results").listFiles().orEmpty()
      .filter { it.extension == "json" }.map { JSONObject(it.readText()) }.sortedBy { it.optInt("sequence") }
    meta.put("results", JSONArray(rows))
  }
  fun result(c: Context, owner: String, id: String, row: JSONObject) {
    val dir = File(root(c, owner), "$id/results").apply { mkdirs() }
    write(File(dir, hash(row.getString("key").toByteArray()) + ".json"), row.toString().toByteArray())
  }
  fun hash(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
  fun hash(file: File): String = file.inputStream().use { input ->
    val digest = MessageDigest.getInstance("SHA-256"); val bytes = ByteArray(64 * 1024)
    while (true) { val n = input.read(bytes); if (n < 0) break; digest.update(bytes, 0, n) }
    digest.digest().joinToString("") { "%02x".format(it) }
  }
  @Synchronized fun write(file: File, bytes: ByteArray) {
    val temp = File(file.parentFile, file.name + ".tmp")
    FileOutputStream(temp).use { it.write(bytes); it.fd.sync() }
    check(temp.renameTo(file)) { "Could not save test progress" }
  }
  @Synchronized fun save(c: Context, owner: String, value: JSONObject) {
    val metadata = JSONObject()
    value.keys().forEach { key -> if (key != "results") metadata.put(key, value.get(key)) }
    write(run(c, owner), metadata.toString().toByteArray())
  }
  fun array(a: JSONArray) = (0 until a.length()).map(a::getJSONObject)
  fun models(c: Context): List<JSONObject> = array(JSONArray(c.assets.open("speech-eval/models.json").bufferedReader().use { it.readText() }))
  fun local(c: Context, id: String) = models(c).first { "local:" + it.getString("id") == id }
  fun bytes(c: Context, file: DocumentFile, max: Int): ByteArray {
    check(file.isFile) { "Choose a dataset folder containing dataset.json and its recordings" }
    return checkNotNull(c.contentResolver.openInputStream(file.uri)).use {
      val data = it.readBytesBounded(max)
      data
    }
  }
  private fun java.io.InputStream.readBytesBounded(max: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream(); val buffer = ByteArray(16384)
    while (true) { val count = read(buffer); if (count < 0) break; require(out.size() + count <= max) { "file_too_large" }; out.write(buffer, 0, count) }
    return out.toByteArray()
  }
  fun import(c: Context, owner: String, folderUri: String, candidates: List<String>, repeats: Int): JSONObject {
    require(candidates.isNotEmpty() && candidates.size <= 48 && candidates.distinct().size == candidates.size)
    require(repeats in 1..5)
    val allowed = models(c).map { "local:" + it.getString("id") } + listOf("server:base.en", "server:small.en", "server:medium.en", "server:large-v3", "server:large-v3-turbo")
    require(candidates.all { it in allowed || SpeechEvalServerCandidate.parse(it) != null }) { "Choose a supported model" }
    val folder = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(folderUri)))
    val manifest = bytes(c, checkNotNull(folder.findFile("dataset.json")) { "Place dataset.json in the selected folder" }, 1024 * 1024)
    val dataset = JSONObject(String(manifest, Charsets.UTF_8))
    require(dataset.getInt("schemaVersion") == 1)
    val samples = dataset.getJSONArray("samples")
    require(samples.length() * candidates.size * (repeats + 1) <= 10000) { "Choose fewer recordings, models or repetitions for this run" }
    require(samples.length() in 1..512) { "Choose between 1 and 512 samples" }
    val id = java.util.UUID.randomUUID().toString()
    val dir = File(root(c, owner), id).apply { mkdirs() }
    try {
      require(dir.usableSpace > samples.length() * 1_000_000L + 128 * 1024 * 1024) { "Free some phone storage first" }
      val seen = mutableSetOf<String>()
      val prepared = JSONArray()
      array(samples).forEachIndexed { i, sample ->
        val name = sample.getString("audioFile")
        require(name.endsWith(".wav") && name.length <= 240 && '/' !in name && '\\' !in name && seen.add(name)) { "Use unique WAV filenames in the dataset folder" }
        sample.optJSONArray("tags")?.let { tags ->
          require(tags.length() <= 16 && (0 until tags.length()).all { tags.get(it) is String && tags.getString(it).length <= 64 }) { "Use up to sixteen short tags per sample" }
        }
        require(sample.has("expectedCommand")) { "Every sample needs expectedCommand, including null for rejection" }
        val command = if (sample.isNull("expectedCommand")) null else sample.getString("expectedCommand")
        require(command == null || GlassesCommand.entries.any { it.key == command })
        require(sample.get("expectedTranscript") is String && sample.getString("expectedTranscript").length <= 2048)
        val audio = bytes(c, checkNotNull(folder.findFile(name)) { "A recording is missing from the dataset folder" }, SpeechEvalPolicy.MAX_WAV_BYTES)
        SpeechEvalPolicy.pcm(audio)
        write(File(dir, "$i.wav"), audio)
        prepared.put(JSONObject(sample.toString()).put("sha256", hash(audio)).put("index", i))
      }
      val value = JSONObject().put("schemaVersion", 1).put("id", id).put("datasetSha256", hash(manifest))
        .put("samples", prepared).put("candidates", JSONArray(candidates)).put("repeats", repeats)
        .put("state", SpeechEvalRunState.READY.key).put("results", JSONArray()).put("matcherVersion", GlassesCommandMatcher.VERSION).put("scorerVersion", SpeechEvalPolicy.VERSION)
        .put("device", android.os.Build.MODEL).put("androidVersion", android.os.Build.VERSION.SDK_INT)
        .put("appVersion", c.packageManager.getPackageInfo(c.packageName, 0).versionName)
        .put("appUpdatedAtMs", c.packageManager.getPackageInfo(c.packageName, 0).lastUpdateTime)
        .put("engineVersion", "whisper.rn-0.5.5").put("createdAtMs", System.currentTimeMillis())
      val previous = read(c, owner)
      save(c, owner, value)
      previous?.optString("id")?.takeIf { it != id && it.matches(Regex("[a-f0-9-]{36}")) }?.let { File(root(c, owner), it).deleteRecursively() }
      return value
    } catch (e: Exception) { dir.deleteRecursively(); throw e }
  }
  fun summary(value: JSONObject): List<Map<String, Any?>> {
    val results = array(value.getJSONArray("results"))
    return (0 until value.getJSONArray("candidates").length()).flatMap { index ->
      val candidate = value.getJSONArray("candidates").getString(index)
      SpeechEvalMode.entries.map { mode ->
        val rows = results.filter { it.getString("candidate") == candidate && it.getString("mode") == mode.key }
        val ok = rows.filter { it.optString("status") == SpeechEvalResult.COMPLETE.key }
        val positives = ok.count { !it.isNull("expectedCommand") }
        val negatives = ok.size - positives
        val correct = ok.count { it.optBoolean("correct") }
        val falseAccepts = ok.count { it.isNull("expectedCommand") && !it.isNull("command") }
        val missed = ok.count { !it.isNull("expectedCommand") && it.isNull("command") }
        val wrong = ok.count { !it.isNull("expectedCommand") && !it.isNull("command") && !it.optBoolean("correct") }
        val times = ok.map { it.getLong("totalMs") }
        val words = ok.sumOf { it.optInt("referenceWords") }
        mapOf("candidate" to candidate, "mode" to mode.key, "count" to rows.size, "completed" to ok.size,
          "failures" to rows.count { it.optString("status") != SpeechEvalResult.COMPLETE.key },
          "failureReasons" to SpeechEvalFailures.counts(rows), "correct" to correct,
          "positives" to positives, "negatives" to negatives, "missed" to missed, "wrong" to wrong,
          "falseAccepts" to falseAccepts, "accuracy" to if (ok.isEmpty()) null else correct.toDouble() / ok.size,
          "wer" to if (words == 0) null else ok.sumOf { it.optInt("wordErrors") }.toDouble() / words,
          "medianMs" to SpeechEvalPolicy.percentile(times, 0.5), "p95Ms" to SpeechEvalPolicy.percentile(times, 0.95))
      }
    }
  }
  fun snapshot(c: Context): Map<String, Any?> {
    val value = read(c, owner(c)) ?: return mapOf("state" to SpeechEvalRunState.EMPTY.key, "completed" to 0, "total" to 0, "summary" to emptyList<Any>())
    val total = value.getJSONArray("samples").length() * value.getJSONArray("candidates").length() * (value.getInt("repeats") + 1)
    return mapOf("state" to value.runState().key, "completed" to value.getJSONArray("results").length(), "total" to total,
      "samples" to value.getJSONArray("samples").length(), "message" to value.optString("message"), "summary" to summary(value))
  }
  fun export(c: Context, owner: String, baseUri: String) {
    val value = checkNotNull(read(c, owner))
    val base = checkNotNull(DocumentFile.fromTreeUri(c, Uri.parse(baseUri)))
    val exports = base.findFile("Exports") ?: checkNotNull(base.createDirectory("Exports"))
    val folder = exports.findFile("Speech tests") ?: checkNotNull(exports.createDirectory("Speech tests"))
    fun writeExport(name: String, mime: String, bytes: ByteArray) {
      val file = folder.findFile(name) ?: checkNotNull(folder.createFile(mime, name))
      checkNotNull(c.contentResolver.openOutputStream(file.uri, "wt")).use { it.write(bytes) }
      require(hash(bytes) == hash(bytes(c, file, bytes.size + 1))) { "Could not verify exported results" }
    }
    val id = value.getString("id")
    val export = JSONObject(value.toString()).put("summary", JSONArray(summary(value)))
    writeExport("speech-$id.json", "application/json", export.toString(2).toByteArray())
    val rows = array(value.getJSONArray("results"))
    val columns = listOf("candidate", "sample", "mode", "repeat", "status", "error", "failureStage", "httpStatus", "upstreamHttpStatus", "preparationDurationMs", "correct", "expectedCommand", "command", "totalMs", "modelLoadMs", "inferenceMs", "inferenceCpuMs", "cpuThreads", "roundTripMs", "wordErrors", "referenceWords", "text")
    fun cell(value: String): String {
      val safe = if (value.trimStart().firstOrNull() in listOf('=', '+', '-', '@', '\t', '\r')) "'" + value else value
      return "\"" + safe.replace("\"", "\"\"") + "\""
    }
    val csv = columns.joinToString(",") + "\n" + rows.joinToString("\n") { row -> columns.joinToString(",") { cell(row.optString(it, "")) } }
    writeExport("speech-$id.csv", "text/csv", csv.toByteArray())
  }
}
