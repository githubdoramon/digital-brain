package expo.modules.digitalbrainruntime

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit

object RuntimeLocationUploadScheduler {
  const val UNIQUE_WORK_NAME = "digital-brain-native-location-upload"

  fun enqueueIfPending(context: Context, reason: String) {
    val queueSize = try {
      RuntimeLocationStore.pendingSamples(context.applicationContext).size
    } catch (error: Exception) {
      RuntimeLocationDiagnostics.record(context, "upload_schedule_queue_read_failed", mapOf(
        "reason" to reason,
        "queue_read_error_type" to error.javaClass.simpleName,
      ))
      return
    }
    if (queueSize > 0) {
      enqueue(context, reason, queueSize)
    } else {
      RuntimeLocationDiagnostics.record(context, "upload_schedule_skipped_empty", mapOf(
        "reason" to reason,
        "queue_size" to 0,
      ))
    }
  }

  fun enqueue(context: Context, reason: String, pendingCount: Int? = null) {
    val request = OneTimeWorkRequestBuilder<RuntimeLocationUploadWorker>()
      .setConstraints(
        Constraints.Builder()
          .setRequiredNetworkType(NetworkType.CONNECTED)
          .build(),
      )
      .setBackoffCriteria(
        androidx.work.BackoffPolicy.EXPONENTIAL,
        5,
        TimeUnit.MINUTES,
      )
      .addTag("digital-brain-location-upload")
      .build()

    RuntimeLocationDiagnostics.record(context, "upload_schedule_requested", mapOf(
      "reason" to reason,
      "queue_size" to (pendingCount ?: runCatching { RuntimeLocationStore.pendingSamples(context).size }.getOrDefault(-1)),
      "work_id" to request.id.toString(),
      "operation_state" to "pending",
      "constraint" to "network_connected",
      "existing_policy" to "APPEND_OR_REPLACE",
    ))
    try {
      val operation = WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
        UNIQUE_WORK_NAME,
        ExistingWorkPolicy.APPEND_OR_REPLACE,
        request,
      )
      operation.result.addListener({
        val accepted = runCatching { operation.result.get() }.isSuccess
        RuntimeLocationDiagnostics.record(context, if (accepted) "upload_schedule_accepted" else "upload_schedule_rejected", mapOf(
          "reason" to reason,
          "work_id" to request.id.toString(),
          "operation_state" to if (accepted) "accepted" else "failed",
        ))
      }, Executor { it.run() })
      Log.i("DigitalBrainRuntime", "native_upload_scheduled reason=$reason work_id=${request.id}")
    } catch (error: Exception) {
      RuntimeLocationDiagnostics.record(context, "upload_schedule_rejected", mapOf(
        "reason" to reason,
        "work_id" to request.id.toString(),
        "operation_state" to "failed",
        "error_type" to error.javaClass.simpleName,
      ))
      Log.e("DigitalBrainRuntime", "native_upload_schedule_failed type=${error.javaClass.simpleName}")
    }
  }

  fun cancel(context: Context) {
    WorkManager.getInstance(context.applicationContext).cancelUniqueWork(UNIQUE_WORK_NAME)
    RuntimeLocationDiagnostics.record(context, "upload_work_cancelled")
  }
}
