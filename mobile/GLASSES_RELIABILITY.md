# Glasses connection, alerts, and firmware

## Capture failures and restart evidence

Physical capture is owned by Mentra Live: the button reaches its ASG process,
which captures locally when gallery mode is enabled. Digital Brain reconciles
the resulting files. Sending a replacement photo command for a received button
press would risk duplicate photos and does not repair a stalled camera process.

Source inspection found two app/SDK recovery gaps:

- Mentra's installed Android SDK sends a ping every 30 seconds but previously
  only logged pongs. A stale link could continue to report ready indefinitely.
  `ControlPlaneWatchdog` now requires a demonstrated pong in the current
  session, then requests recovery only after three unanswered probes and at
  least 90 seconds without a pong. Its event fires once per failed session.
- Pairing previously waited for an existing connection operation but did not
  reserve ownership itself. Foreground/capture work could start while pairing
  was releasing Android's old GATT connection. Pairing and forgetting now hold
  the same owner for their entire operation.

`sdk.ts` performs recovery through that owner: disconnect, allow one second for
Android to release GATT, reconnect the saved device, wait for readiness, then
apply capture defaults. Automatic recovery is limited to two attempts per
15 minutes. The settings repair action provides the same release/reconnect
without removing pairing or rebooting the glasses.

During ASG/BES/MTK firmware work the native watchdog is suppressed. The guard
requires a new pong afterward, including on firmware that never supports pong.
BLE audio and BES heartbeats alone are not evidence that ASG is responding.

The SDK can retain Bluetooth while ASG restarts. Changed process sessions from
both version information and a new readiness message emit a distinct diagnostic
event. Digital Brain logs `glasses_process_restarted`, `glasses_control_ready`,
`glasses_link_unhealthy`, recovery attempts and outcomes. Heartbeat, pong,
readiness, GATT failure and ACK timeout log messages bypass the generic native
log throttle. A restart announcement alone does not identify a crash, OTA
restart, thermal shutdown, or power problem.

These are source-backed recovery fixes. They do not establish the cause of a
specific physical camera/power-button freeze. A wedged glasses OS or camera
driver that cannot answer Bluetooth still may require a glasses restart.

## Alert playback

`GlassesAlertTone.kt` generates shared preview/production PCM. Notification
notes now peak at 0.75 full scale instead of 0.22 and last 180/320 ms. Calls
use a dual-frequency modulated ring, peak bounded below full scale, with
1,200 ms ringing and 400 ms silence in one looping static AudioTrack. The
settings call preview plays three cycles. Call answer/decline stops and
releases the current track immediately. Phone-use and enabled-state checks
continue during the ring.

The track begins muted until Android reports the remembered glasses output.
Route loss stops playback; settings tests bypass unlocked-phone suppression.
The phone's media-volume setting is unchanged, so perceived loudness still
depends on the Bluetooth volume and glasses speakers. Notification source
package filtering, cooldown, and local privacy boundaries remain in force.

## Firmware updates

Settings → Smart glasses → Firmware uses the installed SDK's
`checkForOtaUpdate`, `startOtaUpdate`, `requestVersionInfo`, internal
`sendOtaQueryStatus`, and `ota_status` events. It uses Mentra's SDK-selected
manifest, including the SDK's compatibility handling for older glasses.
Firmware downloads and installation run on the glasses over their own Wi-Fi;
the backend does not host or proxy firmware.

Check availability before offering Install. Confirm in the app, require a
ready connection, glasses Wi-Fi, at least 50% battery, idle camera/gallery,
and idle audio/capture/connection workers. Installing the SDK-matched version
can include compatibility changes, not only a version-number upgrade.

`maintenance.ts` persists ownership before dispatch. Camera drains retain
pending jobs without consuming retries, microphone activation and ordinary
connection/configuration changes are blocked, and wake listening is paused.
Progress listeners live outside the settings screen. A 15-second status poll
reattaches after screen navigation or app restart. Start-ACK timeouts retain
ownership and never replay `ota_start`. An early idle response cannot release
an ambiguous start. Completion/failure comes from the glasses; a start ACK,
100% intermediate step, or BLE reconnect is not completion. Recheck versions
after the final reboot to verify compatibility or discover a remaining step.
Cold-process status recovery may reacquire an idle saved Bluetooth link through
the connection owner, without cancelling a boot, resetting the controller, or
applying capture settings. Old-session progress callbacks cannot clear a new
installation's maintenance barrier.
Status polling stops after the glasses report a terminal update outcome. It
does not request fresh version information in that same operation, because
the expected final reboot can temporarily disconnect the glasses. A later
explicit Check for updates verifies the installed versions.

## Verification

Run from `mobile`:

```sh
npm run test:glasses-reliability
npm run test:glasses-command
npx tsc --noEmit
```

Compile the Android modules, then test their actual Kotlin bytecode:

```sh
cd android
./gradlew :digital-brain-glasses-alerts:compileDebugKotlin :mentra-bluetooth-sdk:compileDebugKotlin
cd ..
npm run test:glasses-native
```

Behavior tests cover connection ownership and recovery limits; update
prerequisites, persisted maintenance, start-ACK/terminal races, interruption
and no replay; watchdog grace, legacy firmware and OTA; and generated audio
amplitude, cadence, sustained energy and clipping. Preserve the SDK changes
in `patches/@mentra+bluetooth-sdk+0.1.21-beta.5.patch`.

Physical validation remains necessary: no Android device was attached during
this investigation. Install a native build and test photo/video buttons before
and after reconnect and ASG restart, ordinary/locked-phone alerts and call
answer/decline, Bluetooth removal during ringing, perceived volume, and an
explicitly approved real firmware installation. Export Mentra diagnostics
before resetting a device when reproducing a freeze.

The implementation session passed both Android module compilations, the three
test commands above, full TypeScript checking, focused ESLint, formatting, and
patch-application/byte-match verification. The Smart glasses settings screen
has seven unused Wi-Fi imports from pre-existing work; those unrelated imports
were preserved, so linting that whole screen still reports them. No APK was
installed and no firmware was flashed during this investigation. Native changes
require a new Android build before physical validation.
An Android Metro/Hermes export also passed, covering the integrated JavaScript
bundle and its assets. A regression test proves that a terminal OTA query
retains completion instead of reporting failure when the glasses immediately
restart.

A complete ARM64 release APK was subsequently built with the current local
Android signing configuration. `:app:assembleRelease` passed, the APK signature
verified, and its DEX/Hermes contents included the watchdog, new alert PCM,
firmware screen, maintenance state, and connection repair action. This is a
local test artifact; it was not installed or distributed. Installation over an
existing app must use the matching signing identity without deleting app data.
Physical button/restart reproduction, speaker loudness/call behavior, and a real
firmware installation remain unverified because ADB reports no attached device.
