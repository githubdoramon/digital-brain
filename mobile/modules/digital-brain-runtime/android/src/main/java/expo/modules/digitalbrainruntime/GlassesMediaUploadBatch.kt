package expo.modules.digitalbrainruntime

import java.util.concurrent.CancellationException

/** One failed original must not block gallery discovery or unrelated uploads. */
internal class GlassesMediaUploadBatch(private val checkActive: () -> Unit) {
  private val failed = mutableSetOf<String>()
  private var firstFailure: Exception? = null

  fun attempt(key: String, upload: () -> Unit): Boolean {
    checkActive()
    if (key in failed) return false
    return try {
      upload()
      true
    } catch (e: CancellationException) {
      throw e
    } catch (e: Exception) {
      checkActive()
      failed.add(key)
      if (firstFailure == null) firstFailure = e
      false
    }
  }

  fun throwIfFailed() { firstFailure?.let { throw it } }
}
