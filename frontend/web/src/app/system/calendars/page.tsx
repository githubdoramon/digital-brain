"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Provider = "google" | "microsoft";
type CalendarOption = { id: string; summary: string; primary: boolean };
type ConnectionStatus = {
  connected: boolean;
  user_email?: string;
  connected_email?: string;
  selected_calendar_ids?: string[];
  last_sync_at?: string | null;
  last_reconciliation_at?: string | null;
  last_error?: string | null;
};

const PROVIDERS: Record<Provider, { title: string; accountLabel: string; connectLabel: string }> = {
  google: {
    title: "Google Calendar",
    accountLabel: "Google account",
    connectLabel: "Connect Google Calendar",
  },
  microsoft: {
    title: "Microsoft 365 Calendar",
    accountLabel: "Microsoft 365 account",
    connectLabel: "Connect Microsoft 365 Calendar",
  },
};

const OAUTH_ERRORS: Record<string, string> = {
  oauth_config: "Calendar connection is not configured yet.",
  sign_in_required: "Sign in again before connecting a calendar.",
  invalid_oauth_state: "The connection request expired. Please try again.",
  authorization_failed: "Calendar access was not granted.",
  token_exchange_failed: "The provider could not complete the Calendar connection.",
  account_check_failed: "Digital Brain could not verify the connected account.",
  account_mismatch: "Connect using the Google account you use to sign in to Digital Brain.",
  connection_save_failed: "Digital Brain could not save the Calendar connection.",
  connection_failed: "The Calendar connection could not be completed. Please try again.",
};

function formatDate(value?: string | null): string {
  if (!value) return "Not yet";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function CalendarConnectionCard({ provider }: { provider: Provider }) {
  const details = PROVIDERS[provider];
  const apiRoot = `/integrations/${provider}-calendar`;
  const [connection, setConnection] = useState<ConnectionStatus | null>(null);
  const [calendars, setCalendars] = useState<CalendarOption[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const status = await api.get<ConnectionStatus>(`${apiRoot}/connection`);
      setConnection(status);
      const query = new URLSearchParams(window.location.search);
      const queryProvider = query.get("provider");
      if (queryProvider === provider && query.get("error")) {
        setError(OAUTH_ERRORS[query.get("error") ?? ""] ?? "The Calendar connection could not be completed.");
      }
      if (queryProvider === provider && query.get("connected")) {
        setNotice(`${details.title} is connected. Choose the calendars to sync.`);
      }
      if (status.connected) {
        const response = await api.get<{ calendars: CalendarOption[] }>(`${apiRoot}/calendars`);
        setCalendars(response.calendars ?? []);
        setSelected(status.selected_calendar_ids ?? []);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load Calendar settings.");
    } finally {
      setLoading(false);
    }
  }, [apiRoot, details.title, provider]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const toggleCalendar = (calendarId: string) => {
    setSelected((current) => current.includes(calendarId)
      ? current.filter((item) => item !== calendarId)
      : [...current, calendarId]);
  };

  const saveCalendars = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.put<ConnectionStatus>(`${apiRoot}/calendars`, { calendarIds: selected });
      setConnection(updated);
      setNotice("Calendar selection saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the calendar selection.");
    } finally {
      setSaving(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm(`Disconnect ${details.title}? Synced meetings will remain in Digital Brain.`)) return;
    setSaving(true);
    setError(null);
    try {
      await api.delete(`${apiRoot}/connection`);
      setConnection({ connected: false });
      setCalendars([]);
      setSelected([]);
      setNotice(`${details.title} disconnected. Synced meetings remain available.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not disconnect ${details.title}.`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section style={{ display: "grid", gap: 16, padding: 20, background: "white", borderRadius: 12 }}>
      <div style={{ display: "grid", gap: 8 }}>
        <h2 style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0 }}>{details.title}</h2>
        <p style={{ color: "#555", margin: 0 }}>
          Choose which calendars Digital Brain keeps in sync with your meetings.
        </p>
      </div>

      {error && <p role="alert" style={{ color: "#a12622", margin: 0 }}>{error}</p>}
      {notice && <p role="status" style={{ color: "#236b3b", margin: 0 }}>{notice}</p>}
      {loading ? (
        <p>Loading Calendar settings…</p>
      ) : !connection?.connected ? (
        <div>
          <button
            type="button"
            onClick={() => { window.location.assign(`/api/${provider}-calendar/connect`); }}
          >
            {details.connectLabel}
          </button>
        </div>
      ) : (
        <>
          <div>
            <strong>Connected {details.accountLabel.toLowerCase()}</strong>
            <div>{provider === "microsoft" ? connection.connected_email : connection.user_email}</div>
            <div style={{ color: "#666", fontSize: 14, marginTop: 8 }}>
              Last sync: {formatDate(connection.last_sync_at)}
              {connection.last_reconciliation_at && ` · Weekly check: ${formatDate(connection.last_reconciliation_at)}`}
            </div>
            {connection.last_error && (
              <div role="status" style={{ color: "#a12622", fontSize: 14, marginTop: 8 }}>
                Sync issue: {connection.last_error}
              </div>
            )}
          </div>

          <fieldset style={{ border: 0, borderTop: "1px solid #ddd", padding: "16px 0 0", margin: 0 }}>
            <legend style={{ fontWeight: 600, padding: "0 8px 0 0" }}>Calendars to sync</legend>
            {calendars.length === 0 ? <p>No calendars were found for this account.</p> : (
              <div style={{ display: "grid", gap: 10 }}>
                {calendars.map((calendar) => (
                  <label key={calendar.id} style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={selected.includes(calendar.id)}
                      onChange={() => toggleCalendar(calendar.id)}
                    />
                    <span>{calendar.summary}{calendar.primary ? " (primary)" : ""}</span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>

          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <button type="button" onClick={() => void saveCalendars()} disabled={saving}>
              {saving ? "Saving…" : "Save calendars"}
            </button>
            <button type="button" onClick={() => void disconnect()} disabled={saving}>
              Disconnect
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export default function CalendarSettingsPage() {
  return (
    <main style={{ display: "grid", gap: 24, maxWidth: 760 }}>
      <div style={{ display: "grid", gap: 8 }}>
        <Link href="/system" style={{ color: "#0b6bcb", width: "fit-content" }}>
          Back to system status
        </Link>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 600, margin: 0 }}>Calendar Sync</h1>
        <p style={{ color: "#555", margin: 0 }}>
          Connect calendar providers and choose which calendars Digital Brain keeps in sync.
        </p>
      </div>
      <CalendarConnectionCard provider="google" />
      <CalendarConnectionCard provider="microsoft" />
    </main>
  );
}
