# Mentra Live original-media sync

## Behavior

Android automatically checks media after connection readiness and debounced photo/video/button signals. Settings → Glasses shows pending capture count, a short status, and **Sync now**. Android WorkManager owns discovery, original downloads, proxy uploads, and cleanup while the UI is closed. The runtime neither takes pictures nor changes capture settings or firmware. There is no idle Wi-Fi polling.

The phone first tries the glasses' reported station IP over an existing Wi-Fi network. If the gallery is unavailable, it starts the glasses hotspot and requests a scoped local Wi-Fi network. Only gallery HTTP requests use that network; uploads retain Android's internet routing and may use cellular. Hotspot credentials stay transient and native. The app releases its local network request and disables a hotspot it started after a run. An ownership marker supports cleanup on the next connection after interruption. Existing user-started hotspots are preserved.

Firmware without a station-mode gallery listener needs hotspot transfers. We do not enable a persistent LAN gallery server. Android 10+ is needed for hotspot joining; Nearby Wi-Fi permission (Android 13+) or fine-location permission (older Android) is required. The first hotspot approval may need **Sync now** with the screen open. Android force-stop and OEM background restrictions can delay work until the app is reopened.

## Durable delivery and cleanup

1. Native code queries gallery counts over BLE, skips camera-busy responses, and reads paginated v3 manifests or the legacy gallery. Only finished supported original images/videos are selected; thumbnails, brackets, and sidecars are not uploaded as independent assets.
2. Account- and glasses-scoped capture records are written atomically in private app storage. Original bytes stream to disk with bounded buffers. V3 range downloads resume and verify range boundaries, ETag, size, and glasses SHA-256; legacy downloads restart after interruption. Videos are never buffered wholly in memory or re-encoded.
3. Before downloading an original, the phone checks its durable backend receipt. Phone tombstones also suppress retained/restored captures. Receipt lookup failure does not discard an original; downloads can be cached pending internet recovery.
4. Uploads use one-MiB chunks and server-confirmed offsets. Repeated identical chunks are idempotent; conflicting bytes and out-of-order writes fail. Backend staging survives container restart through its persistent volume. A SHA-256 mismatch prevents any Immich upload.
5. The backend streams the original to Immich with stable account/content identity and an Immich SHA-1 checksum. It verifies the returned asset is readable and belongs to the configured existing album. An existing nearest phone location sample within ten minutes is attached when a v3 capture timestamp is available. Legacy timezone-free timestamps are not used to invent coordinates.
6. Only after the backend commits a per-user capture receipt does the phone acknowledge the glasses: v3 `/api/v3/ack` with stable acknowledgement ID, or checked `/api/delete-files` for legacy firmware. Lost cleanup replies retry safely. Phone originals are deleted after cleanup acknowledgement, and local completion tombstones remain for thirty days. Later restored copies still consult the permanent backend ledger.

The backend ledger persists across phone reinstall. Immich checksum/device identity protects response-loss retries between Immich creation and the database commit. A deleted/trashed Immich asset or failed album membership check is not acknowledged as a successful new upload. Receipt retention means later user deletion from Immich does not automatically re-import the glasses copy.

Each run yields after seven minutes; subsequent work continues offsets. Errors use WorkManager backoff and retain originals. Disabled glasses/sign-out cancels work, attempts a bounded three-second app-owned hotspot shutdown before closing BLE, and retains files under their original Google-account hash. A rapid re-enable waits for the previous SDK teardown. A different account cannot upload that queue. Pending cleanup for a different saved pair waits for those glasses to reconnect.

## Backend configuration and rollout

Deploy the backend and migration `0067_glasses_media_receipts.sql` along with the app. All native requests use the configured frontend proxy, never Immich credentials on the phone.

- Existing `IMMICH_SERVER_URL` and `IMMICH_API_KEY` configuration is reused.
- Set `GLASSES_IMMICH_ALBUM_ID` to the existing capture album ID. Optional `GLASSES_IMMICH_ALBUM_NAME` resolves only a single exact existing match; no albums are created. The private local backend environment has been configured by looking up the previous capture destination.
- `GLASSES_MEDIA_UPLOAD_DIR` defaults to `storage/glasses-media`; Compose mounts that directory persistently. Custom paths require a matching persistent mount.
- Originals are limited to two GiB each. Per-account staging reservations are capped at four GiB. Abandoned sessions expire after seven idle days when a session is created; completed receipts remain durable.

Endpoints: `GET /mobile/glasses/media/receipts/{key}`, `POST /mobile/glasses/media/sessions`, `PUT /mobile/glasses/media/sessions/{id}?offset=N`, and `POST /mobile/glasses/media/sessions/{id}/complete`. The frontend mobile proxy streams request bodies. No gallery listing or original bytes are returned by these backend endpoints.

## Device qualification

Static compilation, protocol tests, and production bundling cannot establish radio or battery behavior. Test on the new Android build:

- Photograph and record a video; check automatic pending/status transitions and unchanged original dimensions/bytes in the existing album.
- Test reachable station Wi-Fi, then unavailable station Wi-Fi with hotspot fallback. Approve Android's initial hotspot request in Settings; retry with the screen off.
- Disable internet during a download, then restore cellular. Verify cached originals resume uploading and the hotspot stops after the run.
- Interrupt during a range download, an upload chunk, backend completion, and glasses acknowledgement. Confirm one Immich asset and eventual cleanup.
- Reconnect with retained/restored files; verify receipt/tombstone deduplication. Repeat after reinstall while keeping backend receipts.
- Disable glasses/sign out mid-transfer. Confirm work stops, originals remain private, and another account cannot upload them.
- Compare idle battery diagnostics against the stable baseline, then measure a bounded transfer separately. Confirm neither app nor glasses hotspot remains active after a completed transfer.
