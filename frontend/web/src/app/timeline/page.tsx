"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DailyTimeline,
  TimelineLocation,
  TimelineProposal,
  TimelineSegment,
  enqueueProposedEventsForDay,
  getDailyTimeline,
  runProposedEventsForDay,
} from "@/lib/api";

const TILE_SIZE = 256;
const MAP_HEIGHT = 520;
const MIN_ZOOM = 11;
const MAX_ZOOM = 16;

type ProjectedPoint = {
  id: string;
  x: number;
  y: number;
  location: TimelineLocation;
};

type TileSpec = {
  key: string;
  url: string;
  x: number;
  y: number;
};

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

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function lonToTileX(lon: number, zoom: number): number {
  return ((lon + 180) / 360) * 2 ** zoom;
}

function latToTileY(lat: number, zoom: number): number {
  const radians = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(radians) + 1 / Math.cos(radians)) / Math.PI) / 2) * 2 ** zoom;
}

function formatTime(value: string, timezone: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  });
}

function formatDateTime(value: string, timezone: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  });
}

function formatPlaceName(item: Pick<TimelineLocation | TimelineSegment | TimelineProposal, "place_name" | "city" | "country">): string {
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

function buildMapState(locations: TimelineLocation[]) {
  if (locations.length === 0) {
    return null;
  }

  const lats = locations.map((item) => item.lat);
  const lons = locations.map((item) => item.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const centerLat = (minLat + maxLat) / 2;
  const centerLon = (minLon + maxLon) / 2;
  const latSpan = Math.max(0.004, maxLat - minLat);
  const lonSpan = Math.max(0.004, maxLon - minLon);
  const zoom = clamp(Math.floor(Math.log2(360 / Math.max(latSpan * 1.8, lonSpan * 1.8))), MIN_ZOOM, MAX_ZOOM);
  const centerTileX = lonToTileX(centerLon, zoom);
  const centerTileY = latToTileY(centerLat, zoom);

  return { centerTileX, centerTileY, zoom };
}

function TimelineMap({ locations, segments, timezone }: { locations: TimelineLocation[]; segments: TimelineSegment[]; timezone: string }) {
  const mapState = useMemo(() => buildMapState(locations), [locations]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(912);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      return;
    }
    const resize = () => setWidth(Math.max(320, node.clientWidth));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const { tiles, points, path } = useMemo(() => {
    if (!mapState) {
      return { tiles: [] as TileSpec[], points: [] as ProjectedPoint[], path: "" };
    }

    const centerPixelX = mapState.centerTileX * TILE_SIZE;
    const centerPixelY = mapState.centerTileY * TILE_SIZE;
    const topLeftX = centerPixelX - width / 2;
    const topLeftY = centerPixelY - MAP_HEIGHT / 2;
    const tileCount = 2 ** mapState.zoom;
    const minTileX = Math.floor(topLeftX / TILE_SIZE) - 1;
    const maxTileX = Math.floor((topLeftX + width) / TILE_SIZE) + 1;
    const minTileY = Math.floor(topLeftY / TILE_SIZE) - 1;
    const maxTileY = Math.floor((topLeftY + MAP_HEIGHT) / TILE_SIZE) + 1;

    const nextTiles: TileSpec[] = [];
    for (let tileX = minTileX; tileX <= maxTileX; tileX += 1) {
      for (let tileY = minTileY; tileY <= maxTileY; tileY += 1) {
        const wrappedX = ((tileX % tileCount) + tileCount) % tileCount;
        nextTiles.push({
          key: `${mapState.zoom}-${tileX}-${tileY}`,
          url: `https://tile.openstreetmap.org/${mapState.zoom}/${wrappedX}/${tileY}.png`,
          x: tileX * TILE_SIZE - topLeftX,
          y: tileY * TILE_SIZE - topLeftY,
        });
      }
    }

    const nextPoints = locations.map((location, index) => {
      const pixelX = lonToTileX(location.lon, mapState.zoom) * TILE_SIZE;
      const pixelY = latToTileY(location.lat, mapState.zoom) * TILE_SIZE;
      return {
        id: `${location.id}-${index}`,
        x: pixelX - topLeftX,
        y: pixelY - topLeftY,
        location,
      };
    });

    const nextPath = nextPoints.map((point) => `${point.x},${point.y}`).join(" ");
    return { tiles: nextTiles, points: nextPoints, path: nextPath };
  }, [locations, mapState, width]);

  if (!mapState) {
    return (
      <div className="empty-map">
        <div>No location samples for this day.</div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="map-stage">
      {tiles.map((tile) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={tile.key} src={tile.url} alt="" className="map-tile" style={{ left: tile.x, top: tile.y }} />
      ))}
      <svg className="map-overlay" viewBox={`0 0 ${width} ${MAP_HEIGHT}`} preserveAspectRatio="none" aria-hidden="true">
        <polyline points={path} fill="none" stroke="rgba(10, 28, 43, 0.82)" strokeWidth="4" strokeLinejoin="round" strokeLinecap="round" />
        {segments.map((segment, index) => {
          const point = points.find((candidate) => candidate.location.id === segment.first_sample_id);
          if (!point) {
            return null;
          }
          return (
            <circle
              key={`${segment.signature}-${index}`}
              cx={point.x}
              cy={point.y}
              r={segment.would_propose ? 12 : 8}
              fill={segment.would_propose ? "rgba(18, 184, 134, 0.84)" : "rgba(245, 158, 11, 0.84)"}
              stroke="#fff"
              strokeWidth="3"
            />
          );
        })}
      </svg>
      {points.map((point, index) => (
        <div
          key={point.id}
          className="time-pin"
          style={{ left: point.x, top: point.y }}
          title={`${formatTime(point.location.captured_at, timezone)} · ${formatPlaceName(point.location)}`}
        >
          {index + 1}
        </div>
      ))}
      <div className="map-attribution">© OpenStreetMap contributors</div>
    </div>
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

  const loadTimeline = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextTimeline = await getDailyTimeline(date, timezone);
      setTimeline(nextTimeline);
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
      setNotice(`Run finished: ${result.created ?? 0} created, ${result.skipped ?? 0} skipped.`);
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
  const overlapCount = timeline?.segments.filter((segment) => segment.overlaps_event).length ?? 0;

  return (
    <div className="timeline-page">
      <section className="timeline-hero">
        <div>
          <p className="eyebrow">Location diagnostics</p>
          <h1>Daily timeline</h1>
          <p className="hero-copy">Plot location samples in capture order, inspect stay segments, and compare them with proposed-event output.</p>
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
          <p>Eligible stays</p>
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

      <TimelineMap locations={timeline?.locations ?? []} segments={timeline?.segments ?? []} timezone={timeline?.timezone ?? timezone} />

      <section className="data-grid">
        <div className="diagnostic-panel">
          <div className="panel-heading">
            <h2>Stay Segments</h2>
            <span>{timeline?.window.local_start ? `${formatDateTime(timeline.window.local_start, timeline.timezone)} → ${formatDateTime(timeline.window.local_end, timeline.timezone)}` : ""}</span>
          </div>
          <div className="segment-list">
            {(timeline?.segments ?? []).map((segment, index) => (
              <div key={`${segment.signature}-${segment.start_at}`} className={`segment-row${segment.would_propose ? " eligible" : ""}`}>
                <div className="segment-index">{index + 1}</div>
                <div>
                  <strong>{formatPlaceName(segment)}</strong>
                  <p>{formatTime(segment.start_at, timeline?.timezone ?? timezone)} to {formatTime(segment.end_at, timeline?.timezone ?? timezone)} · {segment.duration_minutes} min · {segment.sample_count} samples</p>
                  <code>{segment.signature}</code>
                </div>
                <span className={`status-pill${segment.would_propose ? " good" : ""}`}>{statusLabel(segment)}</span>
              </div>
            ))}
            {timeline && timeline.segments.length === 0 ? <p className="empty-text">No stay segments were built. At least two samples are needed.</p> : null}
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
                  <p>{formatTime(proposal.start_at, timeline?.timezone ?? timezone)} to {formatTime(proposal.end_at, timeline?.timezone ?? timezone)} · {proposal.duration_minutes} min</p>
                  <p>{proposal.reason || proposal.suggested_summary || "No reason stored."}</p>
                </div>
                <span className="status-pill good">{proposal.status}</span>
              </div>
            ))}
            {timeline && timeline.proposals.length === 0 ? <p className="empty-text">No proposals stored for this local date.</p> : null}
          </div>
        </div>
      </section>

      <style jsx>{`
        .timeline-page {
          display: flex;
          flex-direction: column;
          gap: 20px;
          color: #12202f;
        }

        .timeline-hero {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 360px;
          gap: 24px;
          align-items: end;
          border-bottom: 1px solid rgba(31, 41, 55, 0.12);
          padding-bottom: 18px;
        }

        .eyebrow {
          margin: 0 0 8px;
          color: #28705d;
          font-size: 0.78rem;
          font-weight: 800;
          letter-spacing: 0.08em;
          text-transform: uppercase;
        }

        h1 {
          margin: 0;
          font-size: 2.6rem;
          line-height: 1;
        }

        .hero-copy {
          max-width: 620px;
          margin: 12px 0 0;
          color: #566477;
          font-size: 1rem;
          line-height: 1.5;
        }

        .control-panel {
          display: grid;
          gap: 10px;
          padding: 14px;
          border: 1px solid rgba(45, 55, 72, 0.14);
          border-radius: 8px;
          background: #fff;
          box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
        }

        label {
          display: grid;
          gap: 6px;
          color: #566477;
          font-size: 0.78rem;
          font-weight: 700;
          text-transform: uppercase;
        }

        input {
          width: 100%;
          border: 1px solid rgba(45, 55, 72, 0.18);
          border-radius: 6px;
          padding: 10px 11px;
          color: #12202f;
          font: inherit;
          text-transform: none;
          background: #f9faf8;
        }

        .button-row {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
        }

        button {
          border: 0;
          border-radius: 6px;
          padding: 10px 8px;
          color: #fff;
          font: inherit;
          font-weight: 800;
          background: #19324a;
          cursor: pointer;
        }

        button:nth-child(2) {
          background: #16715d;
        }

        button:nth-child(3) {
          background: #7a4f14;
        }

        button:disabled {
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
          padding: 14px;
          border: 1px solid rgba(45, 55, 72, 0.12);
          border-radius: 8px;
          background: #fff;
        }

        .metric-strip span {
          display: block;
          font-size: 1.7rem;
          font-weight: 900;
        }

        .metric-strip p {
          margin: 3px 0 0;
          color: #667085;
          font-size: 0.84rem;
          font-weight: 700;
        }

        .map-stage,
        .empty-map {
          position: relative;
          height: ${MAP_HEIGHT}px;
          overflow: hidden;
          border: 1px solid rgba(17, 24, 39, 0.16);
          border-radius: 8px;
          background: #dbe8e2;
          box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.45);
        }

        .empty-map {
          display: grid;
          place-items: center;
          color: #566477;
          font-weight: 800;
        }

        .map-tile {
          position: absolute;
          width: ${TILE_SIZE}px;
          height: ${TILE_SIZE}px;
          user-select: none;
        }

        .map-overlay {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.18));
        }

        .time-pin {
          position: absolute;
          display: grid;
          width: 26px;
          height: 26px;
          place-items: center;
          border: 2px solid #fff;
          border-radius: 999px;
          color: #fff;
          font-size: 0.72rem;
          font-weight: 900;
          background: #19324a;
          box-shadow: 0 5px 12px rgba(15, 23, 42, 0.28);
          transform: translate(-50%, -50%);
        }

        .map-attribution {
          position: absolute;
          right: 8px;
          bottom: 8px;
          padding: 4px 7px;
          border-radius: 5px;
          color: #475569;
          font-size: 0.72rem;
          background: rgba(255, 255, 255, 0.82);
        }

        .data-grid {
          display: grid;
          grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
          gap: 16px;
        }

        .diagnostic-panel {
          min-width: 0;
          border: 1px solid rgba(45, 55, 72, 0.12);
          border-radius: 8px;
          background: #fff;
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
          font-weight: 700;
        }

        .segment-list,
        .proposal-list {
          display: grid;
          gap: 0;
        }

        .segment-row,
        .proposal-row {
          display: grid;
          grid-template-columns: 32px minmax(0, 1fr) auto;
          gap: 12px;
          align-items: start;
          padding: 14px 16px;
          border-bottom: 1px solid rgba(45, 55, 72, 0.08);
        }

        .proposal-row {
          grid-template-columns: minmax(0, 1fr) auto;
        }

        .segment-row.eligible {
          background: rgba(18, 184, 134, 0.07);
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
          background: #19324a;
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

        @media (max-width: 860px) {
          .timeline-hero,
          .data-grid {
            grid-template-columns: 1fr;
          }

          .metric-strip {
            grid-template-columns: repeat(2, 1fr);
          }
        }

        @media (max-width: 560px) {
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
