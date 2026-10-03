# Mentra Live connection and firmware maintenance

This restoration supports Android Mentra Live through exact npm version `@mentra/bluetooth-sdk` 3.1.1. It requires a newly built native app; Expo Go and an older installed binary cannot supply the module.

## Ownership and settings

Settings → Glasses starts disabled. Enabling requests Nearby devices permissions (fine location on Android 11 and earlier), Wi-Fi transfer permission, and optional notification permission. Pairing scans for 15 seconds and uses SDK device identity; the selected pair is persisted only after readiness. One pair is supported. Disconnect disables persisted glasses ownership; Forget clears the app's saved identity. The SDK may ask Android to bond the Classic audio device during pairing, Optional alert tones and local recording playback use app-owned audio. User-started glasses microphone capture is available under Recordings; see [the recording contract](GLASSES_RECORDINGS.md).

`RuntimeGlasses` runs on the native main looper, owned by `DigitalBrainRuntimeService` with `connectedDevice` foreground type. Its notification is shared with independent background location. React Native configures settings and reads diagnostics only while interactive. The Settings screen polls only while focused and foregrounded. Native connection callbacks, recovery timers, and boot/package-replacement broadcasts never call JavaScript or start Headless JS. Bluetooth broadcasts are registered and unregistered on the same application context; disable/service teardown cleanup is repeatable and finishes SDK cleanup even if the receiver is already unregistered.

The runtime persists signed-in eligibility separately from enablement. Sign-out stops glasses activity while retaining pair and preference; the next sign-in may resume them. Boot recovery requires enablement, persisted signed-in state, and permissions. Android force-stop, OEM policies, and permission revocation can prevent recovery; opening the app reconciles runtime ownership. No exact reconnect timing or reboot survival is claimed without device validation.

## Reconnect

One connection attempt is bounded to 45 seconds. After unsuccessful attempts, delays are 2s, 5s, 10s, 20s, 40s, 1m, 2m, and 5m, then remain at 5m indefinitely. One minute of sustained readiness resets the backoff. Bluetooth off or missing permissions pauses retry work; restoring Bluetooth or tapping Connect triggers an immediate attempt. Main-looper timers are best-effort during device sleep and never hold a wake lock. Device-presence detection is not implemented in this stage.

The SDK already contains reconnect and foreground-service behavior. A `patch-package` patch opts the application into external connection ownership via manifest metadata. It suppresses the SDK's foreground service and auto-reconnect paths, skips eager Expo SDK construction, unused phone microphone/LC3 encoder/VAD initialization (the LC3 decoder is initialized only for explicit recording) and the ten-second mic timer, and exposes the existing native OTA query for recovery. SDK analytics is disabled in both Expo configuration and the native client. Regenerate/review this version-specific patch on any SDK upgrade; do not add a second reconnect loop.

## Firmware and diagnostics

Every transition to readiness refreshes versions and checks the SDK's release manifest on an IO dispatcher. Availability appears in Settings and an Android notification when permitted. Updates only start after the user taps Update and confirms. The glasses own OTA installation; the app renders `ota_status` progress and persists the in-progress marker to recover status after reconnection or service restart. During updates, pairing, Forget, Wi-Fi changes and ordinary connection controls are disabled. Wi-Fi setup requests an on-demand scan from the connected glasses and presents networks ordered by signal strength with duplicate SSIDs removed. Secured networks ask for a password; open networks do not. Hidden networks retain optional name entry. Scans run only when opening setup or tapping Scan again, never on background timers. Results are rejected if the native SDK session changes or disconnects. Wi-Fi setup sends credentials directly to the SDK without app persistence or diagnostic export. The glasses must have working internet access; old firmware that lacks the protocol reports an error.

Diagnostics retain at most 100 native lifecycle/retry/update events in memory. Export omits device identity and Wi-Fi credentials and includes safe status and OS energy counters. Battery/current and awake/deep-sleep counters are device-wide; process CPU time is app-process time. Neither proves energy attributed to the glasses feature.

## Notification chimes and call alerts

Settings → Glasses → Alerts provides independent notification and call toggles, both initially off. The chime app allow-list starts empty. Users grant Android notification-listener access and, for cellular call state, Phone permission. Settings lists launchable installed apps using a scoped launcher-intent package-visibility query; saved selections remain local. Alert gains start at 25% for chimes and 35% for calls and adjust independently in five-point steps within the Bluetooth media volume. Test chime bypasses the unlocked-phone gate; test call plays three ring cycles and can be stopped. Leaving the screen stops a preview, never a real call.

`GlassesAlertNotificationListenerService` filters package/flags and incoming-call metadata without reading, exporting or persisting notification text, caller identity, numbers, or people. Ordinary chimes require a selected app and a new notification key or a changed notification timestamp without `FLAG_ONLY_ALERT_ONCE`, exclude ongoing/foreground/group-summary notifications, and are suppressed while the screen is on and unlocked, during incoming calls, or within five seconds of the last started chime. Existing inbox notifications are not replayed on listener connection. Identical timestamp reposts do not chime; updated message timestamps can chime even when an app reuses its notification ID. No message content is inspected. Removal permits a future new post with that key.

Cellular ringing uses `TelephonyCallback` on Android 12+ and legacy native call-state callbacks on older versions; idle/answered state stops it. App calls use Android/AndroidX CallStyle incoming metadata or legacy call-category notifications with a full-screen intent, independently of the chime allow-list. Ongoing/screening/missed-call notifications do not ring. An incoming-to-ongoing update or notification removal ends its source; overlapping incoming sources do not cancel each other. Apps without sufficient incoming/terminal metadata cannot be reliably detected; legacy app behavior must be validated on-device. A source expires after two minutes without a terminal event, and repeated incoming updates do not extend its lifetime. These are alert sounds, not answering controls or call-audio transport.

`GlassesAlertPlayback` renders short local PCM tones only when the signed-in glasses runtime is ready, outside OTA. It requests `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`; Android/player policy may pause speech rather than lower it. A static muted loop establishes the preferred Bluetooth output, then playback restarts the full tone only after the actual glasses route is verified. Android 16 multi-output routing must contain only that output. Routing changes or device removal stop playback. The existing connected-device foreground runtime satisfies background focus eligibility; there is no second persistent service or JS/headless task. No periodic call polling, audio recorder, wake lock, SDK phone-audio monitor, or microphone initialization is added.

Silent/vibrate modes do not suppress media audio. **User decision: retain Android Do Not Disturb suppression when media is blocked.** System media mute/zero volume still applies; the UI reports this and offers Android sound settings. No global volume, ringer, or DND policy is modified. Preferred route is matched to the connected/saved glasses Bluetooth name, not a generic brand substring; actual output identity is verified before unmuting. No speaker fallback is intentional. Route loss still requires physical-device validation because Android/OEM audio policy owns the final routing. Completion, focus loss, settings changes, permission/listener disconnection, glasses disconnect/disable, sign-out and service teardown release playback. Automatic previews do not start.

No legacy capture, command, Moments APIs or dropped database tables are restored. Wake words, PCM capture/streaming, media capture/upload, recording, notification content forwarding and scene analysis remain outside this stage.

## Validation

Automated checks cover TypeScript, lint, native compilation/manifest merging, retry cap/reset unit tests, the existing background-location regression harness, and production JavaScript eager bundling (`expo export:embed --eager --platform android --dev false`). A Gradle debug APK build does not validate the production JavaScript bundle; check that phase separately before handing off a full release build. Physical-device validation is still required:

1. Compare the same idle/screen-off period with Glasses disabled, enabled while connected, and enabled with glasses unavailable. Record Android app battery attribution and diagnostics.
2. Pair, verify readiness/battery/version, disable, re-enable, forget, and pair again.
3. Walk out of range, leave the glasses off long enough to hit the five-minute cap, return, and toggle Bluetooth off/on. Confirm one retry owner and no tight scan cycle.
4. Restart the phone while enabled; check native recovery before opening React navigation. Repeat after sign-out; glasses must remain inactive.
5. Verify location with Glasses independently enabled/disabled and vice versa.
6. Check update availability, scan available glasses Wi-Fi (including empty/error results), select secured/open networks, check hidden-network entry, configure Wi-Fi if needed, and perform an explicitly confirmed update. Verify progress, rejection/recovery, glasses reboot/reconnect, and updated version metadata.

7. In Alerts, verify both toggles and the app list start empty/off. Grant notification access and Phone access, select one app, and test the chime/ring with the actual Bluetooth audio output. Confirm saved volumes and settings after process restart.
8. Post new selected/unselected notifications with the phone locked and unlocked; test a burst within five seconds, an update to the same key, group summaries and ongoing notifications. No chime while the phone is on and unlocked; no queued catch-up notifications.
9. Place cellular and representative app calls with the phone both locked and unlocked. Verify repeating ring, stop on answer/decline/end, overlapping sources, missed-call exclusion and the two-minute stale-source limit. Include an app using modern CallStyle and a legacy full-screen call notification.
10. Play music/podcasts during alerts and verify ducking/recovery, silent/vibrate behavior, DND media suppression and muted Bluetooth volume. Disconnect audio/Bluetooth mid-ring, disable glasses/calls, sign out, revoke access and leave a preview screen; verify teardown and no intended phone-speaker fallback. Repeat idle battery comparison with both alert toggles off and on.

## Original photos and videos

Native automatic downloads and Immich uploads are implemented in the separate [media sync contract](GLASSES_MEDIA.md). This does not enable app-initiated captures or change firmware. Settings → Glasses includes a compact pending/status card and Sync now.

## Local audio recording

Settings → Glasses → Recordings supports explicit Start/Stop, M4A output in the selected Digital Brain folder, playback, rename and confirmed deletion. Capture and saving stay native while the screen is locked. Calls/disconnects stop and save without resuming. This adds no backend API or upload. See [GLASSES_RECORDINGS.md](GLASSES_RECORDINGS.md) for storage, permission, recovery and device validation details.
