"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DailyTimeline,
  TimelineLocation,
  TimelineProposal,
  TimelineSegment,
  enqueueProposedEventsForDay,
  getClientConfig,
  getDailyTimeline,
  runProposedEventsForDay,
} from "@/lib/api";

const GOOGLE_MAPS_SCRIPT_ID = "digital-brain-google-maps";
const GOOGLE_MAPS_CALLBACK = "__digitalBrainGoogleMapsReady";
const DEFAULT_CENTER = { lat: 39.5, lng: -8.0 };

type GoogleLatLng = { lat: number; lng: number };
type GoogleMap = {
  fitBounds: (bounds: GoogleLatLngBounds, padding?: number) => void;
  setCenter: (center: GoogleLatLng) => void;
  setZoom: (zoom: number) => void;
};
type GoogleLatLngBounds = {
  extend: (point: GoogleLatLng) => void;
  isEmpty: () => boolean;
};
type GoogleMarker = {
  setMap: (map: GoogleMap | null) => void;
  addListener: (eventName: string, handler: () => void) => void;
};
type GooglePolyline = {
  setMap: (map: GoogleMap | null) => void;
};
type GoogleInfoWindow = {
  setContent: (content: string) => void;
  open: (options: { anchor: GoogleMarker; map: GoogleMap }) => void;
};
type GoogleMapsApi = {
  Map: new (element: HTMLElement, options: Record<string, unknown>) => GoogleMap;
  LatLngBounds: new () => GoogleLatLngBounds;
  Marker: new (options: Record<string, unknown>) => GoogleMarker;
  Polyline: new (options: Record<string, unknown>) => GooglePolyline;
  InfoWindow: new () => GoogleInfoWindow;
  Size: new (width: number, height: number) => unknown;
  SymbolPath: { CIRCLE: number };
};

declare global {
  interface Window {
    google?: { maps: GoogleMapsApi };
    gm_authFailure?: () => void;
    __digitalBrainGoogleMapsReady?: () => void;
  }
}

function todayLocalDate(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset();
  return new Date(now.getTime() - offset * 60_000).toISOString().slice(0, 10);
}

function getBrowserTimezone(): string {
  if (typeof Intl === "undefined") {
    return "UTC";
  }
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

function formatTime(value: string | null | undefined, timezone: string): string {
  if (!value) {
    return "Not recorded";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: timezone,
  });
}

function formatDateTime(value: string | null | undefined, timezone: string): string {
  if (!value) {
    return "Not recorded";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: timezone,
  });
}

function formatPlaceName(
  item: Pick<TimelineLocation | TimelineSegment | TimelineProposal, "place_name" | "city" | "country">
): string {
  const name = item.place_name || "Unknown place";
  const area = [item.city, item.country].filter(Boolean).join(", ");
  return area ? `${name} · ${area}` : name;
}

function statusLabel(segment: TimelineSegment): string {
  if (segment.would_propose) {
    return "Eligible";
  }
  return segment.skip_reason.replaceAll("_", " ");
}

function formatSkipReasons(skipReasons?: Record<string, number>): string {
  if (!skipReasons || Object.keys(skipReasons).length === 0) {
    return "no skip reasons";
  }
  return Object.entries(skipReasons)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([reason, count]) => `${reason}: ${count}`)
    .join(", ");
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function pointInfoHtml(location: TimelineLocation, index: number, timezone: string): string {
  const place = escapeHtml(formatPlaceName(location));
  const captured = escapeHtml(formatDateTime(location.captured_at, timezone));
  const uploaded = escapeHtml(formatDateTime(location.updated_at, timezone));
  const source = escapeHtml(location.source || "unknown");
  const accuracy = location.accuracy_m == null ? "unknown" : `${Math.round(location.accuracy_m)} m`;
  const coords = `${location.lat.toFixed(6)}, ${location.lon.toFixed(6)}`;
  return `
    <div style="min-width:220px;max-width:300px;color:#172033;font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif">
      <div style="font-size:12px;font-weight:800;color:#256b5b;text-transform:uppercase;letter-spacing:.06em">Point ${index + 1}</div>
      <div style="font-size:15px;font-weight:800;margin:4px 0 8px">${place}</div>
      <div style="display:grid;gap:5px;font-size:12px;line-height:1.35">
        <div><strong>Captured:</strong> ${captured}</div>
        <div><strong>Uploaded:</strong> ${uploaded}</div>
        <div><strong>Accuracy:</strong> ${escapeHtml(accuracy)}</div>
        <div><strong>Source:</strong> ${source}</div>
        <div><strong>Coords:</strong> ${escapeHtml(coords)}</div>
      </div>
    </div>
  `;
}

function useGoogleMaps() {
  const [maps, setMaps] = useState<GoogleMapsApi | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingConfig, setLoadingConfig] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const previousReady = window[GOOGLE_MAPS_CALLBACK];
    const previousAuthFailure = window.gm_authFailure;

    async function loadMaps() {
      setLoadingConfig(true);
      try {
        const config = await getClientConfig();
        if (cancelled) {
          return;
        }
        const apiKey = config.googleMapsApiKey?.trim();
        if (!apiKey) {
          setError("Set NEXT_PUBLIC_GOOGLE_MAPS_API_KEY in the frontend container environment.");
          return;
        }

        if (window.google?.maps) {
          setMaps(window.google.maps);
          setError(null);
          return;
        }

        window.gm_authFailure = () => {
          if (!cancelled) {
            setError(
              "Google Maps rejected the API key. Check Maps JavaScript API enablement, billing, and HTTP referrer restrictions for this domain."
            );
            setLoadingConfig(false);
          }
          previousAuthFailure?.();
        };

        const existingScript = document.getElementById(GOOGLE_MAPS_SCRIPT_ID) as HTMLScriptElement | null;
        window[GOOGLE_MAPS_CALLBACK] = () => {
          if (cancelled) {
            return;
          }
          if (window.google?.maps) {
            setMaps(window.google.maps);
            setError(null);
          } else {
            setError("Google Maps loaded without the expected API object.");
          }
          setLoadingConfig(false);
          previousReady?.();
        };

        function handleError() {
          if (!cancelled) {
            setError("Google Maps failed to load. Check the API key, billing, and allowed origins.");
            setLoadingConfig(false);
          }
        }

        if (existingScript && window.google?.maps) {
          setMaps(window.google.maps);
          setError(null);
          setLoadingConfig(false);
          return;
        }

        const script =
          existingScript ||
          Object.assign(document.createElement("script"), {
            id: GOOGLE_MAPS_SCRIPT_ID,
            src: `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(
              apiKey
            )}&v=weekly&loading=async&callback=${GOOGLE_MAPS_CALLBACK}`,
            async: true,
            defer: true,
          });

        script.addEventListener("error", handleError);
        if (!existingScript) {
          document.head.appendChild(script);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load client configuration.");
        }
      } finally {
        if (!cancelled) {
          setLoadingConfig(false);
        }
      }
    }

    void loadMaps();

    return () => {
      cancelled = true;
      if (window[GOOGLE_MAPS_CALLBACK] === previousReady) {
        return;
      }
      window[GOOGLE_MAPS_CALLBACK] = previousReady;
      window.gm_authFailure = previousAuthFailure;
    };
  }, []);

  return { maps, error, loadingConfig };
}

function MapFrame({ children, empty = false }: { children: ReactNode; empty?: boolean }) {
  return (
    <div className={`map-shell${empty ? " empty-map" : ""}`}>
      {children}
      <style jsx>{`
        .map-shell {
          position: relative;
          min-height: 620px;
          height: 68vh;
          overflow: hidden;
          border: 1px solid rgba(17, 24, 39, 0.18);
          border-radius: 8px;
          background: #d7e4e7;
        }

        .empty-map {
          display: grid;
          place-items: center;
          padding: 28px;
          color: #4b5563;
          font-weight: 750;
          text-align: center;
        }

        .empty-map p {
          margin: 8px 0 0;
          color: #667085;
          font-weight: 600;
        }

        @media (max-width: 980px) {
          .map-shell {
            min-height: 520px;
            height: 64vh;
          }
        }
      `}</style>
    </div>
  );
}

function TimelineMap({
  locations,
  timezone,
  selectedLocationId,
  onSelectLocation,
}: {
  locations: TimelineLocation[];
  timezone: string;
  selectedLocationId?: number | null;
  onSelectLocation: (location: TimelineLocation) => void;
}) {
  const { maps, error, loadingConfig } = useGoogleMaps();
  const [mapRenderError, setMapRenderError] = useState<string | null>(null);
  const mapElementRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<GoogleMap | null>(null);
  const markersRef = useRef<GoogleMarker[]>([]);
  const polylineRef = useRef<GooglePolyline | null>(null);
  const infoWindowRef = useRef<GoogleInfoWindow | null>(null);

  useEffect(() => {
    if (!maps || !mapElementRef.current || mapRef.current) {
      return;
    }

    mapRef.current = new maps.Map(mapElementRef.current, {
      center: DEFAULT_CENTER,
      zoom: 7,
      clickableIcons: false,
      fullscreenControl: true,
      mapTypeControl: true,
      streetViewControl: false,
      styles: [
        { featureType: "poi.business", stylers: [{ visibility: "off" }] },
        { featureType: "transit", stylers: [{ visibility: "off" }] },
      ],
    });
    infoWindowRef.current = new maps.InfoWindow();
  }, [maps]);

  useEffect(() => {
    const map = mapRef.current;
    if (!maps || !map) {
      return;
    }

    markersRef.current.forEach((marker) => marker.setMap(null));
    markersRef.current = [];
    polylineRef.current?.setMap(null);
    polylineRef.current = null;

    if (locations.length === 0) {
      map.setCenter(DEFAULT_CENTER);
      map.setZoom(7);
      return;
    }

    const path = locations.map((location) => ({ lat: location.lat, lng: location.lon }));
    const bounds = new maps.LatLngBounds();
    path.forEach((point) => bounds.extend(point));

    polylineRef.current = new maps.Polyline({
      path,
      geodesic: true,
      strokeColor: "#155e75",
      strokeOpacity: 0.92,
      strokeWeight: 4,
      icons: [
        {
          icon: {
            path: "M 0,-1 0,1",
            strokeColor: "#ffffff",
            strokeOpacity: 0.9,
            strokeWeight: 2,
          },
          offset: "0",
          repeat: "22px",
        },
      ],
      map,
    });

    locations.forEach((location, index) => {
      const isSelected = location.id === selectedLocationId;
      const marker = new maps.Marker({
        position: { lat: location.lat, lng: location.lon },
        map,
        title: `${formatTime(location.captured_at, timezone)} · ${formatPlaceName(location)}`,
        icon: {
          path: maps.SymbolPath.CIRCLE,
          scale: isSelected ? 8 : 5,
          fillColor: isSelected ? "#dc2626" : "#0f766e",
          fillOpacity: 0.96,
          strokeColor: "#ffffff",
          strokeWeight: 2,
        },
        zIndex: isSelected ? 1000 : index,
      });
      marker.addListener("click", () => {
        onSelectLocation(location);
        infoWindowRef.current?.setContent(pointInfoHtml(location, index, timezone));
        infoWindowRef.current?.open({ anchor: marker, map });
      });
      markersRef.current.push(marker);
    });

    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, 72);
    }
  }, [locations, maps, onSelectLocation, selectedLocationId, timezone]);

  useEffect(() => {
    if (!maps || !mapElementRef.current || error) {
      return;
    }

    const node = mapElementRef.current;
    const checkForGoogleError = () => {
      if (node.querySelector(".gm-err-container")) {
        setTimeout(() => {
          if (node.querySelector(".gm-err-container")) {
            setMapRenderError(
              "Google Maps rendered an internal error. Check that the key allows this exact domain, Maps JavaScript API is enabled, and billing is active."
            );
          }
        }, 0);
      }
    };
    const observer = new MutationObserver(checkForGoogleError);
    observer.observe(node, { childList: true, subtree: true });
    checkForGoogleError();
    return () => observer.disconnect();
  }, [error, maps]);

  if (error || mapRenderError) {
    return (
      <MapFrame empty>
        <div>
          <strong>Google Maps is not rendering.</strong>
          <p>{error || mapRenderError}</p>
        </div>
      </MapFrame>
    );
  }

  if (loadingConfig || !maps) {
    return (
      <MapFrame empty>
        <div>{loadingConfig ? "Loading map configuration..." : "Loading Google Maps..."}</div>
      </MapFrame>
    );
  }

  return (
    <MapFrame>
      <div ref={mapElementRef} className="google-map" />
      <style jsx>{`
        .google-map {
          width: 100%;
          height: 100%;
        }
      `}</style>
    </MapFrame>
  );
}

export default function TimelinePage() {
  const [date, setDate] = useState(todayLocalDate);
  const [timezone, setTimezone] = useState(getBrowserTimezone);
  const [timeline, setTimeline] = useState<DailyTimeline | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<"run" | "enqueue" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedLocation, setSelectedLocation] = useState<TimelineLocation | null>(null);

  const loadTimeline = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextTimeline = await getDailyTimeline(date, timezone);
      setTimeline(nextTimeline);
      setSelectedLocation(nextTimeline.locations[0] ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load timeline");
    } finally {
      setLoading(false);
    }
  }, [date, timezone]);

  useEffect(() => {
    void loadTimeline();
  }, [loadTimeline]);

  async function runNow() {
    setActionLoading("run");
    setNotice(null);
    setError(null);
    try {
      const result = await runProposedEventsForDay(date, timezone);
      setNotice(
        `Run finished: ${result.created ?? 0} created, ${result.skipped ?? 0} skipped (${formatSkipReasons(
          result.skip_reasons
        )}).`
      );
      await loadTimeline();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run proposed-events job");
    } finally {
      setActionLoading(null);
    }
  }

  async function enqueue() {
    setActionLoading("enqueue");
    setNotice(null);
    setError(null);
    try {
      await enqueueProposedEventsForDay(date, timezone);
      setNotice("Queued proposed-events scan for this day.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to enqueue proposed-events job");
    } finally {
      setActionLoading(null);
    }
  }

  const eligibleCount = timeline?.segments.filter((segment) => segment.would_propose).length ?? 0;
  const overlapCount =
    timeline?.segments.filter((segment) => segment.skip_reason === "overlapping_event").length ?? 0;
  const currentTimezone = timeline?.timezone ?? timezone;
  const selectedLocationId = selectedLocation?.id ?? null;

  return (
    <div className="timeline-page">
      <section className="timeline-header">
        <div>
          <p className="eyebrow">Location diagnostics</p>
          <h1>Daily timeline</h1>
          <p className="hero-copy">Google Maps trace of captured samples, stay segments, and proposed-event output.</p>
        </div>
        <div className="control-panel" aria-label="Timeline controls">
          <label>
            Day
            <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
          </label>
          <label>
            Timezone
            <input value={timezone} onChange={(event) => setTimezone(event.target.value)} />
          </label>
          <div className="button-row">
            <button type="button" onClick={loadTimeline} disabled={loading || actionLoading !== null}>
              {loading ? "Loading" : "Refresh"}
            </button>
            <button type="button" onClick={runNow} disabled={loading || actionLoading !== null}>
              {actionLoading === "run" ? "Running" : "Run Now"}
            </button>
            <button type="button" onClick={enqueue} disabled={loading || actionLoading !== null}>
              {actionLoading === "enqueue" ? "Queueing" : "Enqueue"}
            </button>
          </div>
        </div>
      </section>

      {error ? <div className="alert error">{error}</div> : null}
      {notice ? <div className="alert notice">{notice}</div> : null}

      <section className="metric-strip">
        <div>
          <span>{timeline?.location_count ?? 0}</span>
          <p>Samples</p>
        </div>
        <div>
          <span>{timeline?.segment_count ?? 0}</span>
          <p>Segments</p>
        </div>
        <div>
          <span>{eligibleCount}</span>
          <p>Eligible now</p>
        </div>
        <div>
          <span>{timeline?.proposals.length ?? 0}</span>
          <p>Stored proposals</p>
        </div>
        <div>
          <span>{overlapCount}</span>
          <p>Event overlaps</p>
        </div>
      </section>

      <section className="map-layout">
        <TimelineMap
          locations={timeline?.locations ?? []}
          timezone={currentTimezone}
          selectedLocationId={selectedLocationId}
          onSelectLocation={setSelectedLocation}
        />
        <aside className="point-panel">
          <div className="panel-heading">
            <h2>Selected Point</h2>
            <span>{selectedLocation ? `#${selectedLocation.id}` : "None"}</span>
          </div>
          {selectedLocation ? (
            <div className="point-detail">
              <strong>{formatPlaceName(selectedLocation)}</strong>
              <dl>
                <div>
                  <dt>Captured</dt>
                  <dd>{formatDateTime(selectedLocation.captured_at, currentTimezone)}</dd>
                </div>
                <div>
                  <dt>Uploaded</dt>
                  <dd>{formatDateTime(selectedLocation.updated_at, currentTimezone)}</dd>
                </div>
                <div>
                  <dt>Accuracy</dt>
                  <dd>{selectedLocation.accuracy_m == null ? "Unknown" : `${Math.round(selectedLocation.accuracy_m)} m`}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>{selectedLocation.source || "Unknown"}</dd>
                </div>
                <div>
                  <dt>Coordinates</dt>
                  <dd>{selectedLocation.lat.toFixed(6)}, {selectedLocation.lon.toFixed(6)}</dd>
                </div>
              </dl>
            </div>
          ) : (
            <p className="empty-text">Click any marker to inspect capture and upload details.</p>
          )}
        </aside>
      </section>

      <section className="data-grid">
        <div className="diagnostic-panel">
          <div className="panel-heading">
            <h2>Stay Segments</h2>
            <span>
              {timeline?.window.local_start
                ? `${formatDateTime(timeline.window.local_start, currentTimezone)} to ${formatDateTime(
                    timeline.window.local_end,
                    currentTimezone
                  )}`
                : ""}
            </span>
          </div>
          <div className="segment-list">
            {(timeline?.segments ?? []).map((segment, index) => (
              <button
                type="button"
                key={`${segment.signature}-${segment.start_at}`}
                className={`segment-row${segment.would_propose ? " eligible" : ""}`}
                onClick={() => {
                  const match = timeline?.locations.find((location) => location.id === segment.first_sample_id);
                  if (match) {
                    setSelectedLocation(match);
                  }
                }}
              >
                <div className="segment-index">{index + 1}</div>
                <div>
                  <strong>{formatPlaceName(segment)}</strong>
                  <p>
                    {formatTime(segment.start_at, currentTimezone)} to {formatTime(segment.end_at, currentTimezone)} ·{" "}
                    {segment.duration_minutes} min · {segment.sample_count} samples
                  </p>
                  <code>{segment.signature}</code>
                  {segment.overlapping_events && segment.overlapping_events.length > 0 ? (
                    <div className="overlap-evidence">
                      <span>Overlaps</span>
                      {segment.overlapping_events.map((event) => (
                        <div key={event.id || `${event.start_at}-${event.title}`}>
                          <strong>{event.title || event.id || "Untitled event"}</strong>
                          <p>
                            {formatTime(event.start_at, currentTimezone)} to{" "}
                            {formatTime(event.end_at, currentTimezone)}
                            {event.id ? ` · ${event.id}` : ""}
                          </p>
                          {event.overlap_decision ? (
                            <p>
                              {event.overlap_decision.confidence} confidence ·{" "}
                              {event.overlap_decision.reason}
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
                <span className={`status-pill${segment.would_propose ? " good" : ""}`}>{statusLabel(segment)}</span>
              </button>
            ))}
            {timeline && timeline.segments.length === 0 ? (
              <p className="empty-text">No stay segments were built. At least two samples are needed.</p>
            ) : null}
          </div>
        </div>

        <div className="diagnostic-panel">
          <div className="panel-heading">
            <h2>Proposals</h2>
            <span>{timeline?.proposals.length ?? 0} stored</span>
          </div>
          <div className="proposal-list">
            {(timeline?.proposals ?? []).map((proposal) => (
              <div key={proposal.proposal_id} className="proposal-row">
                <div>
                  <strong>{proposal.suggested_title || formatPlaceName(proposal)}</strong>
                  <p>
                    {formatTime(proposal.start_at, currentTimezone)} to {formatTime(proposal.end_at, currentTimezone)} ·{" "}
                    {proposal.duration_label || `${proposal.duration_minutes} min`}
                  </p>
                  {proposal.suggested_summary ? <p>{proposal.suggested_summary}</p> : null}
                  {proposal.reason ? <p>Why suggested: {proposal.reason}</p> : null}
                </div>
                <span className="status-pill good">{proposal.status}</span>
              </div>
            ))}
            {timeline && timeline.proposals.length === 0 ? (
              <p className="empty-text">No proposals stored for this local date.</p>
            ) : null}
          </div>
        </div>
      </section>

      <style jsx>{`
        .timeline-page {
          display: flex;
          flex-direction: column;
          gap: 18px;
          color: #172033;
        }

        .timeline-header {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 390px;
          gap: 24px;
          align-items: end;
          border-bottom: 1px solid rgba(31, 41, 55, 0.12);
          padding-bottom: 18px;
        }

        .eyebrow {
          margin: 0 0 8px;
          color: #256b5b;
          font-size: 0.78rem;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }

        h1 {
          margin: 0;
          font-size: 2.45rem;
          line-height: 1;
        }

        .hero-copy {
          max-width: 680px;
          margin: 10px 0 0;
          color: #5f6b7a;
          font-size: 1rem;
          line-height: 1.45;
        }

        .control-panel,
        .diagnostic-panel,
        .point-panel,
        .metric-strip div {
          border: 1px solid rgba(45, 55, 72, 0.13);
          border-radius: 8px;
          background: #fff;
          box-shadow: 0 10px 24px rgba(15, 23, 42, 0.045);
        }

        .control-panel {
          display: grid;
          gap: 10px;
          padding: 14px;
        }

        label {
          display: grid;
          gap: 6px;
          color: #5f6b7a;
          font-size: 0.76rem;
          font-weight: 800;
          text-transform: uppercase;
        }

        input {
          width: 100%;
          border: 1px solid rgba(45, 55, 72, 0.2);
          border-radius: 6px;
          padding: 10px 11px;
          color: #172033;
          font: inherit;
          text-transform: none;
          background: #fbfcfd;
        }

        .button-row {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
        }

        .button-row button {
          border: 0;
          border-radius: 6px;
          padding: 10px 8px;
          color: #fff;
          font: inherit;
          font-weight: 800;
          background: #172033;
          cursor: pointer;
        }

        .button-row button:nth-child(2) {
          background: #0f766e;
        }

        .button-row button:nth-child(3) {
          background: #9a5b08;
        }

        .button-row button:disabled {
          opacity: 0.58;
          cursor: wait;
        }

        .alert {
          border-radius: 8px;
          padding: 12px 14px;
          font-weight: 700;
        }

        .alert.error {
          color: #7f1d1d;
          background: #fee2e2;
        }

        .alert.notice {
          color: #14532d;
          background: #dcfce7;
        }

        .metric-strip {
          display: grid;
          grid-template-columns: repeat(5, 1fr);
          gap: 10px;
        }

        .metric-strip div {
          padding: 13px 14px;
        }

        .metric-strip span {
          display: block;
          font-size: 1.55rem;
          font-weight: 900;
        }

        .metric-strip p {
          margin: 3px 0 0;
          color: #667085;
          font-size: 0.82rem;
          font-weight: 750;
        }

        .map-layout {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 310px;
          gap: 14px;
          align-items: stretch;
        }

        .point-panel {
          min-width: 0;
          overflow: hidden;
        }

        .panel-heading {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 14px 16px;
          border-bottom: 1px solid rgba(45, 55, 72, 0.1);
        }

        h2 {
          margin: 0;
          font-size: 1rem;
        }

        .panel-heading span {
          color: #667085;
          font-size: 0.78rem;
          font-weight: 750;
        }

        .point-detail {
          padding: 16px;
        }

        .point-detail strong {
          display: block;
          margin-bottom: 14px;
          overflow-wrap: anywhere;
        }

        dl {
          display: grid;
          gap: 12px;
          margin: 0;
        }

        dl div {
          display: grid;
          gap: 3px;
        }

        dt {
          color: #667085;
          font-size: 0.72rem;
          font-weight: 850;
          text-transform: uppercase;
        }

        dd {
          margin: 0;
          color: #172033;
          font-size: 0.9rem;
          overflow-wrap: anywhere;
        }

        .data-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.12fr) minmax(300px, 0.88fr);
          gap: 14px;
        }

        .diagnostic-panel {
          min-width: 0;
          overflow: hidden;
        }

        .segment-list,
        .proposal-list {
          display: grid;
          max-height: 620px;
          overflow: auto;
        }

        .segment-row,
        .proposal-row {
          display: grid;
          grid-template-columns: 32px minmax(0, 1fr) auto;
          gap: 12px;
          align-items: start;
          width: 100%;
          padding: 14px 16px;
          border: 0;
          border-bottom: 1px solid rgba(45, 55, 72, 0.08);
          border-radius: 0;
          color: inherit;
          text-align: left;
          background: #fff;
        }

        .segment-row:hover {
          background: #f8fafb;
        }

        .proposal-row {
          grid-template-columns: minmax(0, 1fr) auto;
        }

        .segment-row.eligible {
          background: rgba(15, 118, 110, 0.08);
        }

        .segment-index {
          display: grid;
          width: 28px;
          height: 28px;
          place-items: center;
          border-radius: 999px;
          color: #fff;
          font-size: 0.75rem;
          font-weight: 900;
          background: #172033;
        }

        strong {
          display: block;
          overflow-wrap: anywhere;
        }

        .segment-row p,
        .proposal-row p {
          margin: 4px 0 0;
          color: #667085;
          font-size: 0.86rem;
          line-height: 1.4;
        }

        code {
          display: inline-block;
          max-width: 100%;
          margin-top: 7px;
          overflow-wrap: anywhere;
          color: #475569;
          font-size: 0.76rem;
          background: #f3f5f7;
          padding: 3px 5px;
          border-radius: 5px;
        }

        .overlap-evidence {
          display: grid;
          gap: 6px;
          margin-top: 10px;
          padding: 9px 10px;
          border-left: 3px solid #f59e0b;
          border-radius: 6px;
          background: #fffbeb;
        }

        .overlap-evidence > span {
          color: #92400e;
          font-size: 0.72rem;
          font-weight: 900;
          text-transform: uppercase;
        }

        .overlap-evidence strong {
          color: #713f12;
          font-size: 0.86rem;
        }

        .overlap-evidence p {
          margin: 2px 0 0;
          color: #92400e;
          font-size: 0.78rem;
        }

        .status-pill {
          border-radius: 999px;
          padding: 5px 8px;
          color: #713f12;
          font-size: 0.72rem;
          font-weight: 900;
          text-transform: capitalize;
          background: #fef3c7;
          white-space: nowrap;
        }

        .status-pill.good {
          color: #14532d;
          background: #dcfce7;
        }

        .empty-text {
          margin: 0;
          padding: 18px 16px;
          color: #667085;
          font-weight: 700;
        }

        @media (max-width: 980px) {
          .timeline-header,
          .map-layout,
          .data-grid {
            grid-template-columns: 1fr;
          }
        }

        @media (max-width: 680px) {
          h1 {
            font-size: 2rem;
          }

          .button-row,
          .metric-strip {
            grid-template-columns: 1fr;
          }

          .segment-row,
          .proposal-row {
            grid-template-columns: 1fr;
          }

          .status-pill {
            width: fit-content;
          }
        }
      `}</style>
    </div>
  );
}
