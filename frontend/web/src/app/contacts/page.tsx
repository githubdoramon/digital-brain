'use client';

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Contact = {
  contact_id: string;
  display_name: string;
  aliases: string[];
  birthday: string | null;
  emails: string[];
  phones: string[];
  links: string[];
  tags: string[];
  comments: string;
  relationships: ContactRelationship[];
  external_id?: string | null;
};

type ContactRelationship = {
  relationship_id: string;
  contact_id: string;
  type: string;
  other_type: string | null;
  direction: "incoming" | "outgoing";
  created_at: string | null;
  updated_at: string | null;
};

type RelationshipDraft = {
  relationship_id: string;
  contact_id: string;
  type: string;
  reciprocal_type: string;
};

type Status =
  | { kind: "idle" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatList(items: string[]): string {
  return items.join(", ");
}

function normalizeText(value: string): string {
  if (!value) return "";
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

const RELATIONSHIP_SUGGESTIONS = [
  "Spouse",
  "Partner",
  "Husband",
  "Wife",
  "Parent",
  "Child",
  "Sibling",
  "Friend",
  "Coworker",
  "Manager",
  "Direct Report",
  "Mentor",
  "Mentee",
];

function generateContactId(seed?: string): string {
  const timestamp = Date.now().toString(36);
  if (!seed) {
    return `contact:${timestamp}`;
  }
  const cleaned = seed.trim().toLowerCase();
  const slug = cleaned.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const base = slug ? `${slug}-${timestamp}` : timestamp;
  return `contact:${base}`;
}

function generateRelationshipId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `rel-${crypto.randomUUID()}`;
  }
  return `rel-${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
}

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [searchQuery, setSearchQuery] = useState("");
  const [isSubmitting, setSubmitting] = useState(false);
  
  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [editingContact, setEditingContact] = useState<Contact | null>(null);
  const [formState, setFormState] = useState<{
    contact_id: string;
    display_name: string;
    aliases: string;
    birthday: string;
    emails: string;
    phones: string;
    links: string;
    tags: string;
    comments: string;
    relationships: RelationshipDraft[];
    external_id: string;
  }>({
    contact_id: "",
    display_name: "",
    aliases: "",
    birthday: "",
    emails: "",
    phones: "",
    links: "",
    tags: "",
    comments: "",
    relationships: [],
    external_id: "",
  });

  const contactNameById = useMemo(
    () =>
      contacts.reduce<Record<string, string>>((acc, contact) => {
        acc[contact.contact_id] = contact.display_name;
        return acc;
      }, {}),
    [contacts]
  );

  const filteredContacts = useMemo(() => {
    const query = normalizeText(searchQuery.trim());
    const matches =
      query === ""
        ? contacts
        : contacts.filter((contact) => {
            const nameMatch = normalizeText(contact.display_name).includes(query);
            const aliasMatch = contact.aliases.some((alias) =>
              normalizeText(alias).includes(query)
            );
            const commentMatch = normalizeText(contact.comments || "").includes(query);
            return nameMatch || aliasMatch || commentMatch;
          });

    return [...matches].sort((a, b) => {
      const aNormalized = normalizeText(a.display_name);
      const bNormalized = normalizeText(b.display_name);
      const aExternal = aNormalized.startsWith("external contact");
      const bExternal = bNormalized.startsWith("external contact");

      if (aExternal && !bExternal) return 1;
      if (!aExternal && bExternal) return -1;
      return aNormalized.localeCompare(bNormalized);
    });
  }, [contacts, searchQuery]);

  // Load contacts on mount
  useEffect(() => {
    loadContacts();
  }, []);

  async function loadContacts() {
    setIsLoading(true);
    try {
      const data = await api.get<{ contacts: Contact[] }>("/contacts");
      setContacts(data.contacts || []);
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Failed to load contacts",
      });
    } finally {
      setIsLoading(false);
    }
  }

  function openAddModal() {
    setEditingContact(null);
      setFormState({
        contact_id: generateContactId(),
        display_name: "",
        aliases: "",
        birthday: "",
        emails: "",
        phones: "",
        links: "",
        tags: "",
        comments: "",
        relationships: [],
        external_id: "",
      });
    setShowModal(true);
    setStatus({ kind: "idle" });
  }

  function openEditModal(contact: Contact) {
    setEditingContact(contact);
    setFormState({
      contact_id: contact.contact_id,
      display_name: contact.display_name,
      aliases: formatList(contact.aliases),
      birthday: contact.birthday || "",
      emails: formatList(contact.emails),
      phones: formatList(contact.phones),
      links: formatList(contact.links),
      tags: formatList(contact.tags),
      comments: contact.comments || "",
      relationships: (contact.relationships || [])
        .map((rel) => ({
          relationship_id: rel.relationship_id,
          contact_id: rel.contact_id,
          type: rel.type,
          reciprocal_type: rel.other_type || "",
        })),
      external_id: contact.external_id || "",
    });
    setShowModal(true);
    setStatus({ kind: "idle" });
  }

  function closeModal() {
    setShowModal(false);
    setEditingContact(null);
  }

  const handleChange = (field: string) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
      setFormState((prev) => ({ ...prev, [field]: event.target.value }));
    };

  const addRelationshipDraft = () => {
    setFormState((prev) => ({
      ...prev,
      relationships: [
        ...prev.relationships,
        {
          relationship_id: generateRelationshipId(),
          contact_id: "",
          type: "",
          reciprocal_type: "",
        },
      ],
    }));
  };

  const updateRelationshipDraft = (index: number, update: Partial<RelationshipDraft>) => {
    setFormState((prev) => ({
      ...prev,
      relationships: prev.relationships.map((rel, i) =>
        i === index ? { ...rel, ...update } : rel
      ),
    }));
  };

  const removeRelationshipDraft = (index: number) => {
    setFormState((prev) => ({
      ...prev,
      relationships: prev.relationships.filter((_, i) => i !== index),
    }));
  };

  const relationshipsSection = useMemo(() => {
    const otherContacts = contacts
      .filter((c) => c.contact_id !== formState.contact_id)
      .map((c) => ({ label: `${c.display_name} (${c.contact_id})`, value: c.contact_id }));

    if (otherContacts.length === 0) {
      return (
        <div style={{ fontSize: "0.85rem", color: "#777" }}>
          Add another contact first to create relationships.
        </div>
      );
    }

    return (
      <div style={{ display: "grid", gap: "12px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Relationships</span>
          <button
            type="button"
            onClick={addRelationshipDraft}
            style={{
              background: "#0b6bcb",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              padding: "6px 12px",
              fontSize: "0.8rem",
              cursor: "pointer",
            }}
          >
            + Add Relationship
          </button>
        </div>

        {formState.relationships.length === 0 ? (
          <div style={{ fontSize: "0.85rem", color: "#777" }}>
            No relationships defined yet. Use the button above to add one.
          </div>
        ) : (
          <div style={{ display: "grid", gap: "12px" }}>
            {formState.relationships.map((rel, index) => (
              <div
                key={rel.relationship_id}
                style={{
                  display: "grid",
                  gap: "10px",
                  border: "1px solid #e5e7eb",
                  borderRadius: "8px",
                  padding: "12px",
                  background: "#f9fafb",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 500, fontSize: "0.85rem" }}>Relationship #{index + 1}</span>
                  <button
                    type="button"
                    onClick={() => removeRelationshipDraft(index)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#b91c1c",
                      fontSize: "0.9rem",
                      cursor: "pointer",
                    }}
                    aria-label="Remove relationship"
                  >
                    Remove
                  </button>
                </div>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 500, fontSize: "0.8rem" }}>Related Contact</span>
                  <select
                    value={rel.contact_id}
                    onChange={(event) => updateRelationshipDraft(index, { contact_id: event.target.value })}
                    required
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "6px",
                      padding: "8px 10px",
                      fontSize: "0.9rem",
                    }}
                  >
                    <option value="">-- Select contact --</option>
                    {otherContacts.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                  <span style={{ fontSize: "0.75rem", color: "#6b7280" }}>
                    Choose another contact to link with this one.
                  </span>
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 500, fontSize: "0.8rem" }}>
                    Relationship Type
                  </span>
                  <input
                    type="text"
                    value={rel.type}
                    onChange={(event) => updateRelationshipDraft(index, { type: event.target.value })}
                    list="relationship-type-suggestions"
                    placeholder="e.g. Wife, Manager, Mentor"
                    required
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "6px",
                      padding: "8px 10px",
                      fontSize: "0.9rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 500, fontSize: "0.8rem" }}>
                    Reciprocal Type <span style={{ color: "#9ca3af" }}>(optional)</span>
                  </span>
                  <input
                    type="text"
                    value={rel.reciprocal_type}
                    onChange={(event) => updateRelationshipDraft(index, { reciprocal_type: event.target.value })}
                    placeholder="e.g. Husband, Direct Report"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "6px",
                      padding: "8px 10px",
                      fontSize: "0.9rem",
                    }}
                  />
                </label>
              </div>
            ))}
          </div>
        )}
        <datalist id="relationship-type-suggestions">
          {RELATIONSHIP_SUGGESTIONS.map((suggestion) => (
            <option key={suggestion} value={suggestion} />
          ))}
        </datalist>
      </div>
    );
  }, [contacts, formState.contact_id, formState.relationships]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setStatus({ kind: "idle" });

    const trimmedId = formState.contact_id.trim();
    const contactId = trimmedId || generateContactId(formState.display_name || formState.emails);

    const payload = {
      contact_id: contactId,
      display_name: formState.display_name.trim(),
      aliases: parseList(formState.aliases),
      birthday: formState.birthday || null,
      emails: parseList(formState.emails),
      phones: parseList(formState.phones),
      links: parseList(formState.links),
      tags: parseList(formState.tags),
      comments: formState.comments.trim(),
      external_id: formState.external_id.trim() || null,
      relationships: formState.relationships
        .filter((rel) => rel.contact_id && rel.type)
        .map((rel) => ({
          relationship_id: rel.relationship_id,
          from_contact_id: contactId,
          to_contact_id: rel.contact_id,
          relationship_type: rel.type,
          reciprocal_type: rel.reciprocal_type || null,
        })),
    };

    try {
      await api.post("/ingest/contact", payload);

      setStatus({
        kind: "success",
        message: editingContact
          ? `Contact ${payload.display_name} updated successfully`
          : `Contact ${payload.display_name} created successfully`,
      });
      
      // Reload contacts and close modal
      await loadContacts();
      setTimeout(() => {
        closeModal();
      }, 1000);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unexpected error occurred";
      setStatus({ kind: "error", message });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(contact: Contact) {
    if (!confirm(`Are you sure you want to delete ${contact.display_name}?`)) {
      return;
    }

    try {
      await api.delete(`/contacts/${contact.contact_id}`);

      setStatus({
        kind: "success",
        message: `Contact ${contact.display_name} deleted successfully`,
      });
      
      // Reload contacts
      await loadContacts();
      
      // Clear status after 3 seconds
      setTimeout(() => {
        setStatus({ kind: "idle" });
      }, 3000);
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Failed to delete contact",
      });
    }
  }

  return (
    <section style={{ display: "grid", gap: "24px" }}>
      <div style={{ display: "grid", gap: "8px" }}>
        <h1 style={{ fontSize: "2rem", fontWeight: 600 }}>Contacts</h1>
        <p style={{ color: "#555" }}>
          Manage your personal contacts. Add, edit, or remove people from your
          digital brain.
        </p>
      </div>

      {/* Status Messages */}
      {status.kind === "error" && (
        <div
          role="alert"
          style={{
            background: "#fee2e2",
            border: "1px solid #fca5a5",
            color: "#991b1b",
            borderRadius: "8px",
            padding: "12px 16px",
          }}
        >
          {status.message}
        </div>
      )}
      {status.kind === "success" && (
        <div
          role="status"
          style={{
            background: "#dcfce7",
            border: "1px solid #86efac",
            color: "#166534",
            borderRadius: "8px",
            padding: "12px 16px",
          }}
        >
          {status.message}
        </div>
      )}

      {/* Add Contact Button */}
      <div
        style={{
          display: "flex",
          gap: "12px",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <button
            onClick={openAddModal}
            style={{
              background: "#0b6bcb",
              color: "#fff",
              border: "none",
              borderRadius: "8px",
              padding: "12px 24px",
              fontWeight: 600,
              cursor: "pointer",
              fontSize: "0.95rem",
            }}
          >
            + Add New Contact
          </button>
          <Link
            href="/contacts/merge"
            style={{
              marginLeft: "12px",
              background: "#6366f1",
              color: "#fff",
              borderRadius: "8px",
              padding: "12px 20px",
              fontWeight: 600,
              fontSize: "0.95rem",
              display: "inline-block",
            }}
          >
            Manage Merges
          </Link>
        </div>

        <input
          type="search"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          placeholder="Search by name, alias, or notes"
          aria-label="Search contacts by name, alias, or notes"
          style={{
            border: "1px solid #d0d0d0",
            borderRadius: "8px",
            padding: "10px 12px",
            fontSize: "0.95rem",
            minWidth: "220px",
            maxWidth: "320px",
          }}
        />
      </div>

      {/* Contacts List */}
      <div
        style={{
          border: "1px solid #e2e2e2",
          borderRadius: "12px",
          background: "#fff",
          boxShadow: "0 4px 12px rgba(15, 23, 42, 0.04)",
          overflow: "hidden",
        }}
      >
        {isLoading ? (
          <div style={{ padding: "48px", textAlign: "center", color: "#666" }}>
            Loading contacts...
          </div>
        ) : contacts.length === 0 ? (
          <div
            style={{
              padding: "48px",
              textAlign: "center",
              color: "#999",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "12px",
            }}
          >
            <div style={{ fontSize: "2.5rem" }}>👥</div>
            <p>No contacts yet</p>
            <p style={{ fontSize: "0.85rem", color: "#aaa" }}>
              Click &quot;Add New Contact&quot; to get started
            </p>
          </div>
        ) : filteredContacts.length === 0 ? (
          <div
            style={{
              padding: "48px",
              textAlign: "center",
              color: "#999",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "12px",
            }}
          >
            <div style={{ fontSize: "2rem" }}>🔍</div>
            <p>No contacts match that search.</p>
            <button
              onClick={() => setSearchQuery("")}
              style={{
                background: "#f5f5f5",
                color: "#444",
                border: "1px solid #d0d0d0",
                borderRadius: "6px",
                padding: "8px 14px",
                fontSize: "0.9rem",
                cursor: "pointer",
              }}
            >
              Clear search
            </button>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
              }}
            >
              <thead>
                <tr style={{ background: "#f5f5f5", borderBottom: "2px solid #e2e2e2" }}>
                  <th
                    style={{
                      padding: "16px 20px",
                      textAlign: "left",
                      fontWeight: 600,
                      fontSize: "0.9rem",
                    }}
                  >
                    Name
                  </th>
                  <th
                    style={{
                      padding: "16px 20px",
                      textAlign: "left",
                      fontWeight: 600,
                      fontSize: "0.9rem",
                    }}
                  >
                    Relationships
                  </th>
                  <th
                    style={{
                      padding: "16px 20px",
                      textAlign: "left",
                      fontWeight: 600,
                      fontSize: "0.9rem",
                    }}
                  >
                    Email
                  </th>
                  <th
                    style={{
                      padding: "16px 20px",
                      textAlign: "left",
                      fontWeight: 600,
                      fontSize: "0.9rem",
                    }}
                  >
                    Phone
                  </th>
                  <th
                    style={{
                      padding: "16px 20px",
                      textAlign: "left",
                      fontWeight: 600,
                      fontSize: "0.9rem",
                    }}
                  >
                    Tags
                  </th>
                  <th
                    style={{
                      padding: "16px 20px",
                      textAlign: "right",
                      fontWeight: 600,
                      fontSize: "0.9rem",
                    }}
                  >
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredContacts.map((contact, index) => (
                  <tr
                    key={contact.contact_id}
                    style={{
                      borderBottom:
                        index < filteredContacts.length - 1 ? "1px solid #e2e2e2" : "none",
                      transition: "background 0.2s",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "#f9fafb";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <td style={{ padding: "16px 20px" }}>
                      <div style={{ fontWeight: 600 }}>{contact.display_name}</div>
                    {contact.external_id && (
                      <div style={{ fontSize: "0.75rem", color: "#2563eb", fontWeight: 500 }}>
                        External ID: {contact.external_id}
                      </div>
                    )}
                      {contact.aliases.length > 0 && (
                        <div style={{ fontSize: "0.8rem", color: "#666", marginTop: "2px" }}>
                          {contact.aliases.join(", ")}
                        </div>
                      )}
                      {contact.comments && (
                        <div style={{ fontSize: "0.8rem", color: "#6b7280", marginTop: "6px" }}>
                          {contact.comments.length > 120
                            ? `${contact.comments.slice(0, 120)}...`
                            : contact.comments}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "16px 20px", color: "#555" }}>
                      {contact.relationships.length > 0 ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {contact.relationships.slice(0, 3).map((rel) => {
                            const relatedName =
                              contactNameById[rel.contact_id] || rel.contact_id.split(":").pop();
                            return (
                              <span
                                key={`${rel.relationship_id}-${rel.contact_id}-${rel.direction}`}
                                title={`${rel.type} · ${relatedName}`}
                                style={{
                                  background: "#eef2ff",
                                  color: "#312e81",
                                  padding: "4px 8px",
                                  borderRadius: "4px",
                                  fontSize: "0.75rem",
                                  fontWeight: 500,
                                }}
                              >
                                {rel.type} · {relatedName}
                              </span>
                            );
                          })}
                          {contact.relationships.length > 3 && (
                            <span
                              style={{
                                background: "#f5f5f5",
                                color: "#555",
                                padding: "4px 8px",
                                borderRadius: "4px",
                                fontSize: "0.75rem",
                              }}
                            >
                              +{contact.relationships.length - 3} more
                            </span>
                          )}
                        </div>
                      ) : (
                        <span style={{ color: "#999" }}>None</span>
                      )}
                    </td>
                    <td style={{ padding: "16px 20px", color: "#555", fontSize: "0.9rem" }}>
                      {contact.emails[0] || "-"}
                    </td>
                    <td style={{ padding: "16px 20px", color: "#555", fontSize: "0.9rem" }}>
                      {contact.phones[0] || "-"}
                    </td>
                    <td style={{ padding: "16px 20px", color: "#555", fontSize: "0.85rem" }}>
                      {contact.tags.length > 0
                        ? contact.tags.slice(0, 2).join(", ") +
                          (contact.tags.length > 2 ? "..." : "")
                        : "-"}
                    </td>
                    <td
                      style={{
                        padding: "16px 20px",
                        textAlign: "right",
                        display: "flex",
                        gap: "8px",
                        justifyContent: "flex-end",
                      }}
                    >
                      <button
                        onClick={() => openEditModal(contact)}
                        style={{
                          background: "#f5f5f5",
                          color: "#444",
                          border: "1px solid #d0d0d0",
                          borderRadius: "6px",
                          padding: "6px 12px",
                          fontSize: "0.85rem",
                          cursor: "pointer",
                          fontWeight: 500,
                        }}
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(contact)}
                        style={{
                          background: "#fee2e2",
                          color: "#991b1b",
                          border: "1px solid #fca5a5",
                          borderRadius: "6px",
                          padding: "6px 12px",
                          fontSize: "0.85rem",
                          cursor: "pointer",
                          fontWeight: 500,
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal */}
      {showModal && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px",
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              closeModal();
            }
          }}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: "12px",
              maxWidth: "600px",
              width: "100%",
              maxHeight: "90vh",
              overflow: "auto",
              boxShadow: "0 20px 60px rgba(0, 0, 0, 0.3)",
            }}
          >
            <form onSubmit={handleSubmit}>
              {/* Modal Header */}
              <div
                style={{
                  padding: "24px",
                  borderBottom: "1px solid #e2e2e2",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <h2 style={{ fontSize: "1.5rem", fontWeight: 600, margin: 0 }}>
                  {editingContact ? "Edit Contact" : "Add New Contact"}
                </h2>
                <button
                  type="button"
                  onClick={closeModal}
                  style={{
                    background: "transparent",
                    border: "none",
                    fontSize: "1.5rem",
                    cursor: "pointer",
                    color: "#666",
                    padding: "0",
                    width: "32px",
                    height: "32px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  ×
                </button>
              </div>

              {/* Modal Body */}
              <div style={{ padding: "24px", display: "grid", gap: "16px" }}>
                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                    Contact ID <span style={{ color: "#e11d48" }}>*</span>
                  </span>
                  <input
                    type="text"
                    required
                    value={formState.contact_id}
                    onChange={handleChange("contact_id")}
                    disabled={!!editingContact}
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                      background: editingContact ? "#f5f5f5" : "#fff",
                    }}
                  />
                  <span style={{ fontSize: "0.8rem", color: "#666" }}>
                    Unique identifier (e.g., contact:alice#001)
                  </span>
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                    Display Name <span style={{ color: "#e11d48" }}>*</span>
                  </span>
                  <input
                    type="text"
                    required
                    value={formState.display_name}
                    onChange={handleChange("display_name")}
                    placeholder="John Doe"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                    Relationships
                  </span>
                  {relationshipsSection}
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Birthday</span>
                  <input
                    type="date"
                    value={formState.birthday}
                    onChange={handleChange("birthday")}
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Aliases</span>
                  <input
                    type="text"
                    value={formState.aliases}
                    onChange={handleChange("aliases")}
                    placeholder="Johnny, JD (comma-separated)"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Emails</span>
                  <input
                    type="text"
                    value={formState.emails}
                    onChange={handleChange("emails")}
                    placeholder="john@example.com, jdoe@company.com (comma-separated)"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Phones</span>
                  <input
                    type="text"
                    value={formState.phones}
                    onChange={handleChange("phones")}
                    placeholder="+1234567890, +0987654321 (comma-separated)"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Links</span>
                  <input
                    type="text"
                    value={formState.links}
                    onChange={handleChange("links")}
                    placeholder="https://linkedin.com/in/johndoe (comma-separated)"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>
                    External ID <span style={{ color: "#9ca3af" }}>(optional)</span>
                  </span>
                  <input
                    type="text"
                    value={formState.external_id}
                    onChange={handleChange("external_id")}
                    placeholder="External system person identifier"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Tags</span>
                  <input
                    type="text"
                    value={formState.tags}
                    onChange={handleChange("tags")}
                    placeholder="work, family, important (comma-separated)"
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                    }}
                  />
                </label>

                <label style={{ display: "grid", gap: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Comments</span>
                  <textarea
                    value={formState.comments}
                    onChange={handleChange("comments")}
                    placeholder="Notes, context, reminders..."
                    rows={4}
                    style={{
                      border: "1px solid #d0d0d0",
                      borderRadius: "8px",
                      padding: "10px 12px",
                      fontSize: "0.95rem",
                      resize: "vertical",
                    }}
                  />
                </label>

                {status.kind === "error" && (
                  <div
                    role="alert"
                    style={{
                      background: "#fee2e2",
                      border: "1px solid #fca5a5",
                      color: "#991b1b",
                      borderRadius: "8px",
                      padding: "12px 16px",
                      fontSize: "0.9rem",
                    }}
                  >
                    {status.message}
                  </div>
                )}
                {status.kind === "success" && (
                  <div
                    role="status"
                    style={{
                      background: "#dcfce7",
                      border: "1px solid #86efac",
                      color: "#166534",
                      borderRadius: "8px",
                      padding: "12px 16px",
                      fontSize: "0.9rem",
                    }}
                  >
                    {status.message}
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div
                style={{
                  padding: "24px",
                  borderTop: "1px solid #e2e2e2",
                  display: "flex",
                  gap: "12px",
                  justifyContent: "flex-end",
                }}
              >
                <button
                  type="button"
                  onClick={closeModal}
                  disabled={isSubmitting}
                  style={{
                    background: "transparent",
                    color: "#444",
                    border: "1px solid #cbd5e1",
                    borderRadius: "8px",
                    padding: "10px 20px",
                    cursor: "pointer",
                    fontWeight: 500,
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  style={{
                    background: "#0b6bcb",
                    color: "#fff",
                    border: "none",
                    borderRadius: "8px",
                    padding: "10px 20px",
                    fontWeight: 600,
                    cursor: "pointer",
                    opacity: isSubmitting ? 0.7 : 1,
                  }}
                >
                  {isSubmitting
                    ? "Saving..."
                    : editingContact
                    ? "Update Contact"
                    : "Add Contact"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
