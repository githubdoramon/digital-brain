package expo.modules.digitalbrainruntime

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

object RuntimeLocationUploadScheduler {
  const val UNIQUE_WORK_NAME = "digital-brain-native-location-upload"

  fun enqueueIfPending(context: Context, reason: String) {
    val queueSize = try {
      RuntimeLocationStore.pendingSamples(context.applicationContext).size
    } catch (error: Exception) {

      return
    }
    if (queueSize > 0) {
      enqueue(context, reason, queueSize)
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

    try {
      WorkManager.getInstance(context.applicationContext).enqueueUniqueWork(
        UNIQUE_WORK_NAME,
        ExistingWorkPolicy.APPEND_OR_REPLACE,
        request,
      )
    } catch (error: Exception) {

      Log.e("DigitalBrainRuntime", "native_upload_schedule_failed type=${error.javaClass.simpleName}")
    }
  }

  fun cancel(context: Context) {
    WorkManager.getInstance(context.applicationContext).cancelUniqueWork(UNIQUE_WORK_NAME)

  }
}
