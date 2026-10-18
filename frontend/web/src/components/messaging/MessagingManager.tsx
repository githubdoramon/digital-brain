"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import styles from "./messaging.module.css";

type Context = "work" | "personal" | "mixed" | "unclassified";
type Status = {
  enabled: boolean;
  state: string;
  context: Context;
  messages: number;
  pending: number;
  failed: number;
  configured: boolean;
  password_challenge: string | null;
  error_code: string | null;
};
type Contact = { contact_id: string; display_name: string };
type Identity = {
  identity_id: string;
  external_id: string;
  display_name: string;
  contact_id: string | null;
  contact_name: string | null;
  suggested_contacts: Contact[];
};
type Conversation = {
  conversation_id: string;
  title: string;
  external_id: string;
  effective_context: Context;
};
type Message = {
  message_id: string;
  conversation_title?: string;
  conversation_external_id?: string;
  sender_name: string;
  sender_external_id: string;
  sent_at: string;
  body: string;
  deleted: boolean;
  outgoing: boolean;
  attachments: { type: string; filename?: string }[];
};
const base = "/messaging";
const labels: Record<string, string> = {
  connected: "Connected",
  connecting: "Connecting…",
  pairing: "Ready to pair",
  disconnected: "Disconnected",
  attention: "Connection needs attention",
  needs_pairing: "Link your account again",
  password_required: "Enter your password",
};
const contexts: Context[] = ["work", "personal", "mixed", "unclassified"];
function Classification({
  value,
  disabled,
  onChange,
}: {
  value: Context;
  disabled: boolean;
  onChange: (value: Context) => void;
}) {
  return (
    <select
      aria-label="Work or personal"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as Context)}
    >
      {contexts.map((c) => (
        <option key={c} value={c}>
          {c[0].toUpperCase() + c.slice(1)}
        </option>
      ))}
    </select>
  );
}
function MessageBody({
  message,
  providerName,
}: {
  message: Message;
  providerName: string;
}) {
  return (
    <>
      <div className={styles.messageHeading}>
        <strong>
          {message.outgoing
            ? "You"
            : message.sender_name || message.sender_external_id}
        </strong>
        <time dateTime={message.sent_at}>
          {new Date(message.sent_at).toLocaleString()}
        </time>
      </div>
      {message.deleted && (
        <p className={styles.muted}>Deleted in {providerName}</p>
      )}
      <p className={styles.body}>{message.body}</p>
      {message.attachments.map((a, i) => (
        <p key={i} className={styles.muted}>
          {a.filename || a.type} · Find in {providerName}
        </p>
      ))}
    </>
  );
}
export default function MessagingManager({
  provider,
}: {
  provider: "whatsapp" | "telegram";
}) {
  const providerName = provider === "telegram" ? "Telegram" : "WhatsApp";
  const [password, setPassword] = useState("");
  const providerApi = useMemo(() => {
    const scope = (path: string) =>
      path.startsWith(base)
        ? `${path}${path.includes("?") ? "&" : "?"}provider=${provider}`
        : path;
    return {
      get: <T,>(path: string) => api.get<T>(scope(path)),
      put: (path: string, payload: unknown) => api.put(scope(path), payload),
      post: (path: string, payload?: unknown) => api.post(scope(path), payload),
    };
  }, [provider]);
  const [status, setStatus] = useState<Status | null>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [tab, setTab] = useState("connection");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [peopleLoaded, setPeopleLoaded] = useState(false);
  const [filter, setFilter] = useState("unlinked");
  const [peopleQuery, setPeopleQuery] = useState("");
  const [targetContact, setTargetContact] = useState("");
  const [selected, setSelected] = useState<Identity | null>(null);
  const [contactQuery, setContactQuery] = useState("");
  const [newName, setNewName] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasOlder, setHasOlder] = useState(false);
  const [detail, setDetail] = useState<{
    title: string;
    messages: Message[];
  } | null>(null);
  const version = useRef(0);
  const statusVersion = useRef(0);
  const newContactId = useRef("");
  const picker = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (selected)
      picker.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const view = params.get("view");
    if (view && ["connection", "people", "archive"].includes(view))
      setTab(view);
    const contact = params.get("contact_id");
    if (contact) {
      setTargetContact(contact);
      setTab("people");
      setFilter("all");
    }
    let active = true;
    let running = false;
    const load = async () => {
      if (running) return;
      running = true;
      const revision = statusVersion.current;
      try {
        const next = await providerApi.get<Status>(`${base}/status`);
        const qr = next.enabled
          ? await providerApi.get<{ svg: string | null }>(`${base}/pairing`)
          : { svg: null };
        if (active && revision === statusVersion.current) {
          setStatus(next);
          setSvg(qr.svg);
        }
      } catch {
        if (active)
          setError("Could not refresh your connection. Please try again.");
      } finally {
        running = false;
      }
    };
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [providerApi]);
  useEffect(() => {
    if (tab !== "people") return;
    let active = true;
    void Promise.all([
      providerApi.get<{ identities: Identity[] }>(`${base}/identities`),
      providerApi.get<{ contacts: Contact[] }>("/contacts"),
    ])
      .then(([i, c]) => {
        if (active) {
          setIdentities(i.identities);
          setContacts(c.contacts);
          setPeopleLoaded(true);
        }
      })
      .catch(() => {
        if (active)
          setError("Could not load people. Open People again to retry.");
      });
    return () => {
      active = false;
    };
  }, [tab, providerApi]);
  useEffect(() => {
    if (tab !== "archive") return;
    let active = true;
    version.current += 1;
    setDetail(null);
    setMessages([]);
    setHasOlder(false);
    setLoading(true);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        q: query,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      });
      void Promise.all([
        providerApi.get<{ conversations: Conversation[] }>(
          `${base}/conversations`,
        ),
        providerApi.get<{ messages: Message[] }>(`${base}/messages?${params}`),
      ])
        .then(([c, m]) => {
          if (active) {
            setConversations(c.conversations);
            setMessages(m.messages);
            setHasOlder(m.messages.length === 30);
          }
        })
        .catch(() => {
          if (active)
            setError("Could not load your archive. Please try again.");
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 250);
    return () => {
      active = false;
      version.current += 1;
      clearTimeout(timer);
    };
  }, [tab, query, conversationId, providerApi]);
  const act = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch {
      setError("Could not save this change. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  const link = async (identity: Identity, contact: Contact | null) => {
    await providerApi.put(
      `${base}/identities/${encodeURIComponent(identity.identity_id)}/contact`,
      { contact_id: contact?.contact_id ?? null },
    );
    setIdentities((old) =>
      old.map((i) =>
        i.identity_id === identity.identity_id
          ? {
              ...i,
              contact_id: contact?.contact_id ?? null,
              contact_name: contact?.display_name ?? null,
            }
          : i,
      ),
    );
    setSelected(null);
  };
  const createContact = (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !newName.trim()) return;
    const identity = selected;
    void act(async () => {
      const contact = {
        contact_id:
          newContactId.current ||
          (newContactId.current = `contact:${crypto.randomUUID()}`),
        display_name: newName.trim(),
      };
      await providerApi.post("/ingest/contact", {
        ...contact,
        phones: identity.external_id.endsWith("@s.whatsapp.net")
          ? [`+${identity.external_id.split("@")[0]}`]
          : [],
      });
      setContacts((old) => [...old, contact]);
      setNewName("");
      await link(identity, contact);
    });
  };
  const conversation = conversations.find(
    (c) => c.conversation_id === conversationId,
  );
  const visible = identities.filter(
    (i) =>
      (!targetContact || !i.contact_id || i.contact_id === targetContact) &&
      (filter === "all" ||
        (filter === "linked"
          ? !!i.contact_id
          : filter === "suggested"
            ? !i.contact_id && i.suggested_contacts.length > 0
            : !i.contact_id)) &&
      `${i.display_name} ${i.external_id} ${i.contact_name || ""}`
        .toLowerCase()
        .includes(peopleQuery.toLowerCase()),
  );
  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>CONVERSATIONS & PEOPLE</p>
          <h1>Messengers</h1>
          <p className={styles.muted}>
            Connect your conversations to your personal archive.
          </p>
        </div>
        <span className={styles.status}>
          {status
            ? labels[status.state] || "Connection needs attention"
            : "Loading…"}
        </span>
      </header>
      <nav className={styles.providers} aria-label="Messenger">
        {(["whatsapp", "telegram"] as const).map((value) => (
          <Link
            key={value}
            href={{
              pathname: "/system/messengers",
              query: {
                provider: value,
                view: tab,
                ...(targetContact ? { contact_id: targetContact } : {}),
              },
            }}
            aria-current={provider === value ? "page" : undefined}
          >
            {value === "whatsapp" ? "WhatsApp" : "Telegram"}
          </Link>
        ))}
      </nav>
      <nav className={styles.tabs} aria-label={`${providerName} management`}>
        {["connection", "people", "archive"].map((t) => (
          <button
            key={t}
            aria-current={tab === t ? "page" : undefined}
            onClick={() => {
              version.current += 1;
              setTab(t);
              setError("");
            }}
          >
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </nav>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {tab === "connection" && (
        <section className={styles.panel}>
          <h2>{providerName} connection</h2>
          <p>
            {status?.messages ?? 0} archived messages
            {status?.pending ? ` · ${status.pending} waiting to be added` : ""}
          </p>
          <div className={styles.row}>
            <button
              disabled={
                !status || busy || (!status.enabled && !status.configured)
              }
              onClick={() =>
                void act(async () => {
                  if (!status) return;
                  statusVersion.current += 1;
                  await providerApi.put(`${base}/connection`, {
                    enabled: !status.enabled,
                  });
                  setSvg(null);
                  setStatus({
                    ...status,
                    enabled: !status.enabled,
                    state: status.enabled ? "disconnected" : "connecting",
                  });
                })
              }
            >
              {status?.enabled ? "Disconnect" : `Connect ${providerName}`}
            </button>
            {!!status?.failed && (
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await providerApi.post(`${base}/retry`);
                  })
                }
              >
                Retry pending messages
              </button>
            )}
          </div>
          {svg && (
            <div className={styles.pairing}>
              <div>
                <h3>Link your {providerName}</h3>
                <p>
                  {provider === "telegram"
                    ? "On your phone, open Telegram → Settings → Devices → Link Desktop Device, then scan this code."
                    : "On your phone, open WhatsApp → Linked devices → Link a device, then scan this code."}
                </p>
              </div>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
                width={280}
                height={280}
                alt={`${providerName} pairing QR code`}
              />
            </div>
          )}
          {status && !status.configured && (
            <p className={styles.muted}>
              Complete Telegram connection setup, then reload this page to link
              your account.
            </p>
          )}
          {status?.password_challenge && (
            <form
              className={styles.passwordForm}
              onSubmit={(event) => {
                event.preventDefault();
                const secret = password;
                setPassword("");
                void act(async () => {
                  await providerApi.post(`${base}/telegram/password`, {
                    challenge: status.password_challenge,
                    password: secret,
                  });
                });
              }}
            >
              <label>
                Telegram two-step verification password
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  maxLength={256}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              {status.error_code === "password_incorrect" && (
                <p role="alert">That password wasn’t accepted. Try again.</p>
              )}
              <button disabled={busy || !password}>Continue</button>
            </form>
          )}
          <hr />
          <h3>Default context</h3>
          <p className={styles.muted}>
            New conversations use this context. You can change individual
            conversations in the archive.
          </p>
          {status && (
            <Classification
              value={status.context}
              disabled={busy}
              onChange={(context) =>
                void act(async () => {
                  statusVersion.current += 1;
                  await providerApi.put(`${base}/context`, { context });
                  setStatus({ ...status, context });
                })
              }
            />
          )}
        </section>
      )}
      {tab === "people" && (
        <section className={styles.panel}>
          <div className={styles.row}>
            <h2>People</h2>
            <Link href="/contacts">Manage contacts ↗</Link>
          </div>
          <p className={styles.muted}>
            Choose which contact each {providerName} identity belongs to.
          </p>
          {targetContact && (
            <p>
              Linking for{" "}
              {contacts.find((c) => c.contact_id === targetContact)
                ?.display_name || "selected contact"}
              .{" "}
              <button
                onClick={() => {
                  setTargetContact("");
                  window.history.replaceState(
                    null,
                    "",
                    `/system/messengers?provider=${provider}&view=people`,
                  );
                }}
              >
                Show everyone
              </button>
            </p>
          )}
          <div className={styles.row}>
            <input
              aria-label={`Search ${providerName} people`}
              placeholder="Search names or numbers"
              value={peopleQuery}
              onChange={(e) => setPeopleQuery(e.target.value)}
            />
            <select
              aria-label="Filter people"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              <option value="all">Everyone</option>
              <option value="unlinked">Unlinked</option>
              <option value="suggested">Suggested matches</option>
              <option value="linked">Linked</option>
            </select>
          </div>
          {!peopleLoaded && <p role="status">Loading people…</p>}
          {peopleLoaded && !visible.length && <p>No people match this view.</p>}
          {visible.map((i) => (
            <article className={styles.person} key={i.identity_id}>
              <div>
                <strong>{i.display_name || i.external_id}</strong>
                <p className={styles.muted}>{i.external_id}</p>
                <p>
                  {i.contact_name
                    ? `Linked to ${i.contact_name}`
                    : "Not linked"}
                </p>
              </div>
              <div className={styles.row}>
                {!i.contact_id &&
                  i.suggested_contacts.map((c) => (
                    <button
                      disabled={busy}
                      key={c.contact_id}
                      onClick={() => void act(() => link(i, c))}
                    >
                      Link to {c.display_name}
                    </button>
                  ))}
                {targetContact &&
                  !i.contact_id &&
                  contacts.some((c) => c.contact_id === targetContact) && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          link(
                            i,
                            contacts.find(
                              (c) => c.contact_id === targetContact,
                            )!,
                          ),
                        )
                      }
                    >
                      Link to selected contact
                    </button>
                  )}
                <button
                  disabled={busy}
                  onClick={() => {
                    newContactId.current = "";
                    setSelected(i);
                    setContactQuery("");
                    setNewName(i.display_name || "");
                  }}
                >
                  {i.contact_id ? "Change contact" : "Choose contact"}
                </button>
                {i.contact_id && (
                  <button
                    disabled={busy}
                    onClick={() => void act(() => link(i, null))}
                  >
                    Unlink
                  </button>
                )}
              </div>
            </article>
          ))}
          {selected && (
            <section
              className={styles.picker}
              ref={picker}
              aria-label={`Choose contact for ${selected.display_name || selected.external_id}`}
            >
              <div className={styles.row}>
                <h3>Link {selected.display_name || selected.external_id}</h3>
                <button disabled={busy} onClick={() => setSelected(null)}>
                  Cancel
                </button>
              </div>
              <input
                autoFocus
                aria-label="Search contacts"
                placeholder="Find a contact"
                value={contactQuery}
                onChange={(e) => setContactQuery(e.target.value)}
              />
              <div className={styles.contactList}>
                {contacts
                  .filter((c) =>
                    c.display_name
                      .toLowerCase()
                      .includes(contactQuery.toLowerCase()),
                  )
                  .map((c) => (
                    <button
                      key={c.contact_id}
                      disabled={busy}
                      onClick={() => void act(() => link(selected, c))}
                    >
                      {c.display_name}
                    </button>
                  ))}
              </div>
              <form onSubmit={createContact}>
                <label>
                  Create a new contact
                  <input
                    required
                    aria-label="New contact name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                  />
                </label>
                <button disabled={busy || !newName.trim()}>
                  Create and link
                </button>
              </form>
            </section>
          )}
        </section>
      )}
      {tab === "archive" && (
        <section className={styles.panel}>
          <h2>Conversation archive</h2>
          <div className={styles.row}>
            <input
              aria-label="Search messages"
              placeholder="Search what was said"
              value={query}
              onChange={(e) => {
                version.current += 1;
                setQuery(e.target.value);
              }}
            />
            <select
              aria-label="Conversation"
              value={conversationId}
              onChange={(e) => {
                version.current += 1;
                setConversationId(e.target.value);
              }}
            >
              <option value="">All conversations</option>
              {conversations.map((c) => (
                <option key={c.conversation_id} value={c.conversation_id}>
                  {c.title || c.external_id}
                </option>
              ))}
            </select>
          </div>
          {conversation && (
            <div className={styles.row}>
              <p>Conversation context</p>
              <Classification
                value={conversation.effective_context}
                disabled={busy}
                onChange={(context) =>
                  void act(async () => {
                    await providerApi.put(`${base}/context`, {
                      context,
                      conversation_id: conversationId,
                    });
                    setConversations((old) =>
                      old.map((c) =>
                        c.conversation_id === conversationId
                          ? { ...c, effective_context: context }
                          : c,
                      ),
                    );
                  })
                }
              />
            </div>
          )}
          {loading && <p role="status">Loading messages…</p>}
          {!loading && !messages.length && <p>No messages found.</p>}
          {messages.map((m) => (
            <article className={styles.message} key={m.message_id}>
              <p className={styles.muted}>
                {m.conversation_title || m.conversation_external_id}
              </p>
              <MessageBody message={m} providerName={providerName} />
              <button
                disabled={busy}
                onClick={() => {
                  const revision = version.current;
                  void act(async () => {
                    const result = await providerApi.get<{
                      messages: Message[];
                    }>(`${base}/messages/${m.message_id}`);
                    if (revision === version.current)
                      setDetail({
                        title:
                          m.conversation_title ||
                          m.conversation_external_id ||
                          "Conversation",
                        messages: result.messages,
                      });
                  });
                }}
              >
                View exchange
              </button>
            </article>
          ))}
          {conversationId && !query && hasOlder && (
            <button
              disabled={busy || loading}
              onClick={() => {
                const revision = version.current;
                void act(async () => {
                  const params = new URLSearchParams({
                    conversation_id: conversationId,
                    before_id: messages[messages.length - 1].message_id,
                  });
                  const result = await providerApi.get<{ messages: Message[] }>(
                    `${base}/messages?${params}`,
                  );
                  if (revision === version.current) {
                    setMessages((old) => [...old, ...result.messages]);
                    setHasOlder(result.messages.length === 30);
                  }
                });
              }}
            >
              Load older messages
            </button>
          )}
          {detail && (
            <section className={styles.picker} aria-label="Exchange context">
              <div className={styles.row}>
                <h3>{detail.title}</h3>
                <button onClick={() => setDetail(null)}>Close exchange</button>
              </div>
              {detail.messages.map((m) => (
                <article key={m.message_id} className={styles.message}>
                  <MessageBody message={m} providerName={providerName} />
                </article>
              ))}
            </section>
          )}
        </section>
      )}
    </section>
  );
}
