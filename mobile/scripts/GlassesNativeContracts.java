import com.mentra.bluetoothsdk.sgcs.ControlPlaneWatchdog;
import expo.modules.digitalbrainglassesalerts.GlassesAlertTone;
import expo.modules.digitalbrainglassesalerts.RuntimeFeatures;
import expo.modules.digitalbrainglassesalerts.RuntimeFeature;
import expo.modules.digitalbrainglassesalerts.RuntimeWorkCadence;
import java.util.Set;

/** Run against the actual compiled Android Kotlin classes; no mocked PCM/watchdog implementation. */
public class GlassesNativeContracts {
  private static void check(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }

  private static double rms(byte[] pcm, int startMs, int endMs) {
    double sum = 0;
    for (int i = startMs * 48; i < endMs * 48; i++) {
      short sample = (short) ((pcm[i * 2] & 255) | (pcm[i * 2 + 1] << 8));
      check(Math.abs((int) sample) < 32767, "PCM must not clip");
      double normalized = sample / 32767.0;
      sum += normalized * normalized;
    }
    return Math.sqrt(sum / ((endMs - startMs) * 48));
  }

  public static void main(String[] args) {
    RuntimeWorkCadence cadence = new RuntimeWorkCadence();
    check(!cadence.shouldRequest(0, Set.of(RuntimeFeature.CAPTURE)), "Capture opportunities alone must not acquire a JS worker wake lock");
    check(!cadence.shouldRequest(0, Set.of(RuntimeFeature.WAKE, RuntimeFeature.RECORDING, RuntimeFeature.CALL)), "Transient audio owners must not poll location/glasses recovery");
    check(cadence.shouldRequest(0, Set.of(RuntimeFeature.LOCATION)), "Restore durable location handoff on startup");
    cadence.requested(0);
    for (int minute = 1; minute < 15; minute++) check(!cadence.shouldRequest(minute * 60_000L, Set.of(RuntimeFeature.LOCATION)), "Idle location runtime should not wake JS each minute");
    check(cadence.shouldRequest(900_000L, Set.of(RuntimeFeature.LOCATION)), "Retry location delivery after fifteen minutes");
    check(!cadence.shouldRequest(240_000L, Set.of(RuntimeFeature.GLASSES)), "Glasses recovery does not require minute polling");
    check(cadence.shouldRequest(300_000L, Set.of(RuntimeFeature.GLASSES)), "Retain bounded glasses recovery opportunities");
    cadence.requested(300_000L);
    check(!cadence.shouldRequest(360_000L, Set.of(RuntimeFeature.GLASSES)), "Sample-triggered work coalesces the next periodic opportunity");
    RuntimeFeatures owners = new RuntimeFeatures();
    owners.set(RuntimeFeature.LOCATION, true);
    owners.set(RuntimeFeature.GLASSES, true);
    owners.set(RuntimeFeature.WAKE, true);
    owners.set(RuntimeFeature.RECORDING, true);
    owners.set(RuntimeFeature.GLASSES, false);
    check(owners.snapshot().contains(RuntimeFeature.LOCATION), "Glasses disconnect must preserve location");
    owners.set(RuntimeFeature.RECORDING, false);
    check(owners.snapshot().contains(RuntimeFeature.WAKE), "Recorder release must preserve another owner");
    owners.set(RuntimeFeature.CAPTURE, true);
    RuntimeFeatures restored = new RuntimeFeatures();
    restored.restore(owners.persistentKeys());
    check(restored.snapshot().equals(Set.of(RuntimeFeature.LOCATION, RuntimeFeature.CAPTURE)), "Restore durable feature requests only");
    restored.restore(Set.of("location", "recording", "call", "wake", "unknown"));
    check(restored.snapshot().equals(Set.of(RuntimeFeature.LOCATION)), "Never replay transient recording or call work");
    restored.set(RuntimeFeature.LOCATION, false);
    check(restored.snapshot().isEmpty(), "Final owner release permits shutdown");
    ControlPlaneWatchdog watchdog = new ControlPlaneWatchdog(90_000L);
    watchdog.reset(0);
    for (int i = 1; i <= 20; i++) check(!watchdog.probe(i * 30_000L, false), "Legacy non-pong firmware must not reconnect");
    watchdog.pong(610_000L);
    check(!watchdog.probe(640_000L, false), "First missed reply gets grace");
    check(!watchdog.probe(670_000L, false), "Second missed reply gets grace");
    check(!watchdog.probe(700_000L, false), "Send three probes before recovery");
    check(watchdog.probe(730_000L, false), "Silent established ASG session must recover");
    check(!watchdog.probe(760_000L, false), "Only one event per failed session");
    watchdog.reset(800_000L);
    watchdog.pong(810_000L);
    watchdog.probe(840_000L, false);
    watchdog.probe(870_000L, false);
    watchdog.pong(895_000L);
    check(!watchdog.probe(900_000L, false), "Fresh pong cancels missed-reply history");
    for (int i = 1; i <= 50; i++) check(!watchdog.probe(900_000L + i * 30_000L, true), "Never recover during firmware maintenance");
    check(!watchdog.probe(3_000_000L, false), "Resume must re-prove pong support after OTA");

    byte[] notification = GlassesAlertTone.INSTANCE.notification();
    byte[] ring = GlassesAlertTone.INSTANCE.call();
    check(notification.length == 560 * 48 * 2, "Notification duration");
    check(ring.length == 1600 * 48 * 2, "Ring cadence");
    check(rms(notification, 10, 170) > 0.50, "Notification amplitude substantially exceeds old 0.22 peak tone");
    check(rms(notification, 250, 545) > 0.50, "Second chime remains audible");
    check(rms(ring, 15, 1180) > 0.30, "Sustained ring is louder than the old beeps");
    check(rms(ring, 1200, 1600) == 0.0, "Only the final 400 ms are silent");
    for (int ms = 40; ms < 1160; ms += 40) check(rms(ring, ms, ms + 40) > 0.1, "Ring must remain sustained");
    System.out.println("PASS native contracts: idle runtime work cadence, independent ownership/recovery, heartbeat grace/recovery/OTA and alert amplitude/cadence/clipping");
  }
}
