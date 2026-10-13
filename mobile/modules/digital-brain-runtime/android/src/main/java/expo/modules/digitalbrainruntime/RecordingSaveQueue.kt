package expo.modules.digitalbrainruntime

/** Capture reserves a slot; processing/failure keep it until that specific job completes. */
internal class RecordingSaveQueue(private val capacity: Int) {
  private val entries = linkedMapOf<String, RecordingSaveJob>()
  val values get() = entries.values
  val size get() = entries.size
  val hasRoom get() = size < capacity
  operator fun get(id: String) = entries[id]
  operator fun set(id: String, job: RecordingSaveJob) {
    require(id == job.id)
    check(id in entries || hasRoom) { "Recording queue is full" }
    entries[id] = job
  }
  fun restore(job: RecordingSaveJob) { entries[job.id] = job }
  fun nextReady() = values.firstOrNull { it.state == RecordingSaveState.QUEUED }
  fun remove(id: String) = entries.remove(id)
}
