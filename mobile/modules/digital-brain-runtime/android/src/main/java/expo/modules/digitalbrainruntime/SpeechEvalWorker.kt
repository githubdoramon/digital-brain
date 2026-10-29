package expo.modules.digitalbrainruntime

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Native replay only. It deliberately has no dependency on GlassesCommandExecutor. */
class SpeechEvalWorker(c: Context, params: WorkerParameters) : CoroutineWorker(c, params) {
  private val c = applicationContext
  private val client = GlassesTranscriptionClient(c) { guard() }
  private var owner = ""
  private var runId = ""
  private val timeout = Executors.newSingleThreadScheduledExecutor()
  private fun guard() {
    check(!isStopped && RuntimeGlasses.signedIn(c) && GlassesWakeStore.account(c) == owner) { "interrupted" }
    val run = SpeechEvalStore.readMeta(c, owner)
    check(run?.optString("id") == runId && run.runState() in setOf(SpeechEvalRunState.READY, SpeechEvalRunState.RUNNING)) { "interrupted" }
  }
  private fun persist(value: JSONObject) {
    synchronized(SpeechEvalStore) {
      guard()
      SpeechEvalStore.save(c, owner, value)
    }
  }
  override suspend fun doWork(): Result = mutex.withLock {
    withContext(Dispatchers.IO) {
      owner = inputData.getString("owner") ?: return@withContext Result.failure()
      val value = SpeechEvalStore.read(c, owner) ?: return@withContext Result.success()
      runId = value.getString("id")
      if (!RuntimeGlasses.signedIn(c) || GlassesWakeStore.account(c) != owner || value.runState() == SpeechEvalRunState.PAUSED) return@withContext Result.success()
      if (!SpeechEvalServerCandidate.supportsRun(value)) {
        value.put("state", SpeechEvalRunState.PAUSED.key).put("message", "Start a new recognition test")
        SpeechEvalStore.save(c, owner, value)
        return@withContext Result.success()
      }
      try {
        running = true
        timeout.scheduleAtFixedRate({
          if (isStopped || !RuntimeGlasses.signedIn(c) || GlassesWakeStore.account(c) != owner) {
            client.cancel()
          }
        }, 0, 250, TimeUnit.MILLISECONDS)
        setForeground(notification())
        withContext(Dispatchers.Main) { GlassesListening.pauseForComparison() }
        GlassesListening.awaitSpeechWorker()
        guard()
        value.put("state", SpeechEvalRunState.RUNNING.key).put("message", "Preparing recognition test")
        persist(value)
        val deadline = SystemClock.elapsedRealtime() + 6 * 60_000
        val rows = value.getJSONArray("results")
        val existing = SpeechEvalStore.array(rows).map { it.getString("key") }.toMutableSet()
        val samples = SpeechEvalStore.array(value.getJSONArray("samples"))
        val candidates = value.getJSONArray("candidates")
        for (candidateIndex in 0 until candidates.length()) {
          val candidate = candidates.getString(candidateIndex)
          val server = SpeechEvalServerCandidate.parse(candidate)
          val unfinished = samples.any { sample -> (0..value.getInt("repeats")).any { repetition -> "$candidate:${sample.getInt("index")}:$repetition" !in existing } }
          if (!unfinished) continue
          guard()
          val candidateLabel = SpeechEvalServerCandidate.parse(candidate)?.label ?: candidate.removePrefix("local:")
          value.put("message", "Preparing $candidateLabel")
          persist(value)
          var preparationError: Exception? = null
          val preparationStarted = SystemClock.elapsedRealtime()
          try {
            check(SpeechEvalServerCandidate.parse(candidate) != null) { "retired_candidate" }
            client.request(replay = true, prepare = true, candidate = candidate)
          } catch (e: CancellationException) { throw e }
          catch (error: Exception) { guard(); preparationError = error }
          val preparationMs = SystemClock.elapsedRealtime() - preparationStarted
          for (sample in samples) for (repetition in 0..value.getInt("repeats")) {
            val key = "$candidate:${sample.getInt("index")}:$repetition"
            if (key in existing) continue
            guard()
            if (SystemClock.elapsedRealtime() >= deadline) {
              value.put("message", "Continuing shortly"); persist(value)
              return@withContext Result.retry()
            }
            val row = JSONObject().put("key", key).put("candidate", candidate).put("sample", sample.getString("audioFile"))
              .put("matcherVersion", GlassesCommandMatcher.VERSION)
              .put("sampleSha256", sample.getString("sha256")).put("mode", (server?.modes ?: listOf("cold", "warm"))[if (repetition == 0) 0 else 1])
              .put("repeat", repetition).put("expectedCommand", sample.get("expectedCommand"))
              .put("tags", sample.optJSONArray("tags") ?: JSONArray())
            val started = SystemClock.elapsedRealtime()
            try {
              preparationError?.let { throw it }
              val audio = File(SpeechEvalStore.root(c, owner), "$runId/${sample.getInt("index")}.wav").readBytes()
              require(SpeechEvalStore.hash(audio) == sample.getString("sha256")) { "audio_changed" }
              SpeechEvalPolicy.validateAudio(audio)
              if (repetition > 0) {
                val warmupStart = SystemClock.elapsedRealtime()
                client.request(audio, replay = true, candidate = candidate)
                row.put("warmupMs", SystemClock.elapsedRealtime() - warmupStart)
              }
              guard()
              val response = client.request(audio, replay = true, candidate = candidate)
              for (field in response.keys()) row.put(field, response.get(field))
              row.put("totalMs", response.getLong("roundTripMs"))
              val text = response.getString("text")
              guard()
              val matchingAt = SystemClock.elapsedRealtime()
              val decision = GlassesCommandMatcher.match(text)
              val matchingMs = SystemClock.elapsedRealtime() - matchingAt
              row.put("matchingMs", matchingMs).put("totalMs", row.getLong("totalMs") + matchingMs)
              val literal = GlassesCommandMatcher.literal(text)
              val grammar = GlassesCommandMatcher.grammar(text)
              val expected = if (sample.isNull("expectedCommand")) null else sample.getString("expectedCommand")
              row.put("literalCorrect", literal.command?.key == expected)
                .put("grammarCorrect", grammar.command?.key == expected)
              row.put("literalCommand", literal.command?.key ?: JSONObject.NULL)
                .put("grammarCommand", grammar.command?.key ?: JSONObject.NULL)
                .put("matchingStage", decision.stage).put("matchingCost", decision.cost ?: JSONObject.NULL)
                .put("matchingMargin", decision.margin ?: JSONObject.NULL).put("rejectionReason", decision.reason ?: JSONObject.NULL)
              row.put("text", text).put("command", decision.command?.key ?: JSONObject.NULL)
                .put("commandMatch", decision.match.name.lowercase()).put("wakeMatch", decision.wakeMatch.name.lowercase())
                .put("correct", (if (sample.isNull("expectedCommand")) null else sample.getString("expectedCommand")) == decision.command?.key)
                .put("expectedTranscript", sample.getString("expectedTranscript"))
              val errors = SpeechEvalPolicy.errors(sample.getString("expectedTranscript"), text)
              row.put("wordErrors", errors.first).put("referenceWords", errors.second).put("status", SpeechEvalResult.COMPLETE.key)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
              guard()
              row.put("status", SpeechEvalResult.FAILED.key)
                .put("attemptDurationMs", SystemClock.elapsedRealtime() - started)
              SpeechEvalFailures.annotate(row, e)
              if (preparationError != null) row.put("preparationDurationMs", preparationMs)
            } catch (_: OutOfMemoryError) {
              row.put("status", SpeechEvalResult.FAILED.key).put("error", "insufficient_memory")
            }
            row.put("processPssKb", android.os.Debug.getPss())
            val network = c.getSystemService(android.net.ConnectivityManager::class.java)
            val caps = network.getNetworkCapabilities(network.activeNetwork)
            row.put("network", when {
              caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true -> "wifi"
              caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) == true -> "cellular"
              else -> "other"
            })
            synchronized(SpeechEvalStore) {
              guard()
              row.put("sequence", rows.length())
              SpeechEvalStore.result(c, owner, runId, row)
            }
            rows.put(row); existing.add(key)
            value.put("message", "${rows.length()} results saved")
            persist(value)
          }
        }
        value.put("state", SpeechEvalRunState.COMPLETE.key).put("message", "Comparison complete")
        persist(value)
        Result.success()
      } catch (e: CancellationException) {
        pauseJournal()
        throw e
      }
      catch (_: Exception) {
        pauseJournal()
        Result.success()
      } finally {
        running = false
        client.cancel()
        timeout.shutdownNow()
        withContext(kotlinx.coroutines.NonCancellable + Dispatchers.Main) {
          val current = SpeechEvalStore.readMeta(c, owner)
          if (current?.runState() != SpeechEvalRunState.RUNNING) {
            GlassesListening.endComparison()
            if (DigitalBrainRuntime.activityVisible) GlassesListening.resumeForeground(c)
          }
        }
      }
    }
  }
  private fun pauseJournal() {
    synchronized(SpeechEvalStore) {
      val latest = SpeechEvalStore.readMeta(c, owner)
      if (latest?.optString("id") == runId && latest.runState() !in setOf(SpeechEvalRunState.PAUSED, SpeechEvalRunState.COMPLETE)) {
        latest.put("state", SpeechEvalRunState.PAUSED.key).put("message", "Test paused. Resume when you are ready.")
        SpeechEvalStore.save(c, owner, latest)
      }
    }
  }
  private fun notification(): ForegroundInfo {
    val manager = c.getSystemService(NotificationManager::class.java)
    manager.createNotificationChannel(NotificationChannel("speech_eval", "Speech comparisons", NotificationManager.IMPORTANCE_LOW).apply { setSound(null, null) })
    val notification = NotificationCompat.Builder(c, "speech_eval").setSmallIcon(android.R.drawable.ic_dialog_info)
      .setContentTitle("Comparing speech recognition").setContentText("Your test is running. Open Listening to see progress.")
      .setOngoing(true).setSilent(true).build()
    return ForegroundInfo(4320, notification, if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0)
  }
  companion object {
    const val WORK = "glasses-speech-eval"
    private val mutex = Mutex()
    @Volatile var running = false
      private set
    fun enqueue(c: Context, owner: String) {
      WorkManager.getInstance(c).enqueueUniqueWork(WORK, ExistingWorkPolicy.KEEP,
        OneTimeWorkRequestBuilder<SpeechEvalWorker>().setInputData(workDataOf("owner" to owner))
          .setBackoffCriteria(BackoffPolicy.LINEAR, 10, TimeUnit.SECONDS).build())
    }
    fun busy(c: Context) = WorkManager.getInstance(c).getWorkInfosForUniqueWork(WORK).get(5, TimeUnit.SECONDS).any { !it.state.isFinished }
    fun pause(c: Context) {
      val owner = SpeechEvalStore.owner(c)
      synchronized(SpeechEvalStore) {
        SpeechEvalStore.read(c, owner)?.let { if (it.runState() != SpeechEvalRunState.COMPLETE) {
          it.put("state", SpeechEvalRunState.PAUSED.key).put("message", "Test paused"); SpeechEvalStore.save(c, owner, it)
        } }
      }
      WorkManager.getInstance(c).cancelUniqueWork(WORK).result.get(5, TimeUnit.SECONDS)
    }
  }
}
