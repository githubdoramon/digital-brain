package expo.modules.digitalbrainruntime

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class RecordingSaveQueueTest {
  private fun job(id: String) = RecordingSaveJob(id, File("/tmp/fake-recording-$id"), "Recording-$id.m4a", 1, "fake-folder")
  @Test fun encodingDoesNotPreventAnotherCaptureAndEachHasItsOwnSpool() {
    val queue = RecordingSaveQueue(4)
    val first = job("first").apply { state = RecordingSaveState.ENCODING }; queue[first.id] = first
    val second = job("second"); queue[second.id] = second
    assertEquals(RecordingSaveState.CAPTURING, second.state)
    assertNotEquals(first.dir, second.dir)
    queue.remove(first.id)
    assertSame(second, queue[second.id])
  }
  @Test fun failedJobsRemainRecoverableWithoutBlockingOtherQueueSlots() {
    val queue = RecordingSaveQueue(4)
    val failed = job("failed").apply { state = RecordingSaveState.FAILED }; queue[failed.id] = failed
    val queued = job("queued").apply { state = RecordingSaveState.QUEUED }; queue[queued.id] = queued
    assertTrue(queue.hasRoom); assertSame(queued, queue.nextReady()); assertSame(failed, queue[failed.id])
  }
  @Test fun queueBoundIncludesCaptureAndOnlyCompletionFreesASlot() {
    val queue = RecordingSaveQueue(4)
    repeat(4) { n -> queue["$n"] = job("$n") }
    assertFalse(queue.hasRoom)
    queue["0"]!!.state = RecordingSaveState.FAILED
    assertFalse(queue.hasRoom)
    assertThrows(IllegalStateException::class.java) { queue["extra"] = job("extra") }
    queue.remove("1"); assertTrue(queue.hasRoom)
  }
  @Test fun processingNeverSelectsActiveCaptureOrUndrainedAudio() {
    val queue = RecordingSaveQueue(4)
    val capture = job("capture"); queue[capture.id] = capture
    val finishing = job("finishing").apply { state = RecordingSaveState.FINISHING }; queue[finishing.id] = finishing
    assertNull(queue.nextReady())
    finishing.state = RecordingSaveState.QUEUED; assertSame(finishing, queue.nextReady())
  }
  @Test fun stopDrainsOneWriterWhileANewWriterUsesAnIndependentFile() {
    val root = Files.createTempDirectory("recording-queue-test").toFile()
    try {
      val firstDone = CountDownLatch(1); val secondDone = CountDownLatch(1)
      val first = File(root, "first.pcm"); val second = File(root, "second.pcm")
      val errors = java.util.concurrent.atomic.AtomicInteger()
      val writer1 = GlassesPcmWriter(first, { errors.incrementAndGet() }, { firstDone.countDown() }, { System.nanoTime() / 1000000 })
      writer1.start(); assertTrue(writer1.offer(byteArrayOf(1, 2))); writer1.finish()
      val writer2 = GlassesPcmWriter(second, { errors.incrementAndGet() }, { secondDone.countDown() }, { System.nanoTime() / 1000000 })
      writer2.start(); assertTrue(writer2.offer(byteArrayOf(3, 4))); writer2.finish()
      assertTrue(firstDone.await(5, TimeUnit.SECONDS)); assertTrue(secondDone.await(5, TimeUnit.SECONDS))
      assertEquals(0, errors.get()); assertArrayEquals(byteArrayOf(1, 2), first.readBytes())
      assertArrayEquals(byteArrayOf(3, 4), second.readBytes())
      assertFalse(writer1.offer(byteArrayOf(5, 6)))
    } finally { root.deleteRecursively() }
  }
}
