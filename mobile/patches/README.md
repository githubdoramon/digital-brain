# Mentra SDK 3.1.1 patch

The application manifest opts into `com.mentra.bluetoothsdk.external_connection_owner`.
Only opted-in hosts change behavior. Digital Brain uses its own native SDK client and shared foreground runtime instead of the Expo SDK client.

The patch:

- Prevents eager SDK construction from the Expo module.
- Disables the SDK foreground service and its Bluetooth-restored/connection-loss reconnect paths.
- Skips unused phone microphone, LC3/VAD, audio-player and phone-audio-monitor initialization, plus the ten-second mic timer.
- Makes the existing OTA status query public so an interrupted installation can be reconciled after native runtime restart.

It does not disable the device protocol's connection heartbeat, bonding, connection handshake or firmware transport. It does not enable microphone, capture, recording or scene analysis. SDK analytics is separately disabled via Expo configuration and the native SDK config.

`npm install`/`npm ci` applies this patch through the existing `patch-package` postinstall. Review/regenerate it when upgrading the pinned dependency; otherwise upstream SDK ownership can conflict with the application's retry policy.

Regenerate source-only patches with:

```sh
npx patch-package @mentra/bluetooth-sdk --include '^android/src/main/java/'
```

Never include native build outputs or downloaded AARs in the patch.
