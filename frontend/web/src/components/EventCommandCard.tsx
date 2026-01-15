"use client";

import { useState } from "react";

type RelationshipSuggestion = {
  from_contact_id: string;
  from_display_name: string;
  to_contact_id: string;
  to_display_name: string;
  relationship_type: string;
  reciprocal_type: string;
  confidence: string;
  reasoning: string;
};

type EventCommandData = {
  type: "event_confirmation";
  preview_id: string;
  extracted: {
    title: string;
    summary: string;
    when: string | null;
    where: string | null;
    who: string[];
    documents: string[];
    tags: string[];
    types: string[];
  };
  resolution: {
    contacts: Array<{
      contact_id: string;
      display_name: string;
      query: string;
      confidence: string;
    }>;
    places: Array<{
      place_id: string;
      name: string;
    }>;
    documents: Array<{
      document_id: string;
      title: string;
    }>;
    new_entities: {
      contacts: Array<{
        display_name: string;
        query: string;
      }>;
      places: Array<{
        name: string;
        query: string;
      }>;
      documents: Array<{
        reference: string;
      }>;
    };
  };
  relationship_suggestions?: RelationshipSuggestion[];
  message: string;
};

type EventCommandCardProps = {
  commandData: EventCommandData;
  onConfirm: (previewId: string, modifications?: Record<string, unknown>) => void;
  onCancel: () => void;
};

export function EventCommandCard({
  commandData,
  onConfirm,
  onCancel,
}: EventCommandCardProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  // Editable state
  const [title, setTitle] = useState(commandData.extracted.title);
  const [summary, setSummary] = useState(commandData.extracted.summary);
  const [when, setWhen] = useState(commandData.extracted.when || "");
  const [where, setWhere] = useState(commandData.extracted.where || "");
  const [tags, setTags] = useState(commandData.extracted.tags.join(", "));

  // Track which relationship suggestions are selected
  const [selectedRelationships, setSelectedRelationships] = useState<Set<number>>(
    new Set((commandData.relationship_suggestions || []).map((_, idx) => idx))
  );

  const { resolution } = commandData;

  const toggleRelationship = (index: number) => {
    setSelectedRelationships(prev => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const handleConfirm = async () => {
    setIsSubmitting(true);
    try {
      const modifications: Record<string, unknown> = {};

      // Only include modified fields
      if (title !== commandData.extracted.title) {
        modifications.title = title;
      }
      if (summary !== commandData.extracted.summary) {
        modifications.summary = summary;
      }
      if (when !== (commandData.extracted.when || "")) {
        modifications.when = when || null;
      }
      if (where !== (commandData.extracted.where || "")) {
        modifications.where = where || null;
      }

      const newTags = tags.split(",").map(t => t.trim()).filter(t => t);
      const originalTags = commandData.extracted.tags;
      if (JSON.stringify(newTags) !== JSON.stringify(originalTags)) {
        modifications.tags = newTags;
      }

      // Include selected relationship suggestions
      if (commandData.relationship_suggestions && selectedRelationships.size > 0) {
        const confirmedRelationships = Array.from(selectedRelationships)
          .map(idx => commandData.relationship_suggestions?.[idx])
          .filter(Boolean);

        if (confirmedRelationships.length > 0) {
          modifications.confirmed_relationships = confirmedRelationships;
        }
      }

      await onConfirm(commandData.preview_id, modifications);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Format date nicely
  const formatDateTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const today = new Date();
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);

    const isToday = date.toDateString() === today.toDateString();
    const isTomorrow = date.toDateString() === tomorrow.toDateString();

    const timeStr = date.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    });

    if (isToday) return `Today at ${timeStr}`;
    if (isTomorrow) return `Tomorrow at ${timeStr}`;

    return date.toLocaleDateString('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    });
  };

  return (
    <div style={{
      background: 'linear-gradient(to bottom right, #ffffff, #f8fafc)',
      border: '1px solid #e2e8f0',
      borderRadius: '20px',
      boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03)',
      overflow: 'hidden',
      maxWidth: '700px',
      margin: '0 auto',
    }}>
      {/* Modern Header with accent color */}
      <div style={{
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        padding: '24px 28px',
        position: 'relative',
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: '16px',
        }}>
          <div style={{ flex: 1 }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              marginBottom: '8px',
            }}>
              <span style={{
                fontSize: '28px',
                lineHeight: 1,
              }}>📅</span>
              <h3 style={{
                fontSize: '20px',
                fontWeight: '600',
                color: '#ffffff',
                margin: 0,
                letterSpacing: '-0.01em',
              }}>New Event</h3>
            </div>
            <p style={{
              fontSize: '14px',
              color: 'rgba(255, 255, 255, 0.9)',
              margin: 0,
              lineHeight: '1.5',
            }}>{commandData.message}</p>
          </div>
          <button
            onClick={() => setIsEditing(!isEditing)}
            style={{
              background: 'rgba(255, 255, 255, 0.2)',
              backdropFilter: 'blur(10px)',
              border: '1px solid rgba(255, 255, 255, 0.3)',
              borderRadius: '10px',
              padding: '8px 16px',
              fontSize: '14px',
              fontWeight: '500',
              color: '#ffffff',
              cursor: 'pointer',
              transition: 'all 0.2s',
              whiteSpace: 'nowrap',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(255, 255, 255, 0.3)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(255, 255, 255, 0.2)';
            }}
          >
            {isEditing ? '👁️ Preview' : '✏️ Edit'}
          </button>
        </div>
      </div>

      {/* Content with better spacing */}
      <div style={{ padding: '28px' }}>
        {/* Title & Summary - Most prominent */}
        <div style={{ marginBottom: '24px' }}>
          {isEditing ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Event title"
                style={{
                  width: '100%',
                  padding: '14px 16px',
                  fontSize: '18px',
                  fontWeight: '600',
                  color: '#1e293b',
                  background: '#ffffff',
                  border: '2px solid #e2e8f0',
                  borderRadius: '12px',
                  outline: 'none',
                  transition: 'all 0.2s',
                  boxSizing: 'border-box',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = '#667eea';
                  e.currentTarget.style.boxShadow = '0 0 0 3px rgba(102, 126, 234, 0.1)';
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = '#e2e8f0';
                  e.currentTarget.style.boxShadow = 'none';
                }}
              />
              <textarea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="Event description"
                rows={3}
                style={{
                  width: '100%',
                  padding: '14px 16px',
                  fontSize: '15px',
                  color: '#475569',
                  background: '#ffffff',
                  border: '2px solid #e2e8f0',
                  borderRadius: '12px',
                  outline: 'none',
                  resize: 'vertical',
                  fontFamily: 'inherit',
                  lineHeight: '1.6',
                  transition: 'all 0.2s',
                  boxSizing: 'border-box',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = '#667eea';
                  e.currentTarget.style.boxShadow = '0 0 0 3px rgba(102, 126, 234, 0.1)';
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = '#e2e8f0';
                  e.currentTarget.style.boxShadow = 'none';
                }}
              />
            </div>
          ) : (
            <div>
              <h4 style={{
                fontSize: '22px',
                fontWeight: '600',
                color: '#0f172a',
                margin: '0 0 8px 0',
                letterSpacing: '-0.01em',
                lineHeight: '1.3',
              }}>{title}</h4>
              {summary && summary !== title && (
                <p style={{
                  fontSize: '15px',
                  color: '#64748b',
                  margin: 0,
                  lineHeight: '1.6',
                }}>{summary}</p>
              )}
            </div>
          )}
        </div>

        {/* When & Where - Side by side cards */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: when || isEditing ? '1fr 1fr' : '1fr',
          gap: '12px',
          marginBottom: '24px',
        }}>
          {/* When */}
          {(when || isEditing) && (
            <div>
              {isEditing ? (
                <div>
                  <label style={{
                    display: 'block',
                    fontSize: '12px',
                    fontWeight: '600',
                    color: '#64748b',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    marginBottom: '8px',
                  }}>
                    🕐 When
                  </label>
                  <input
                    type="datetime-local"
                    value={when ? new Date(when).toISOString().slice(0, 16) : ""}
                    onChange={(e) => setWhen(e.target.value ? new Date(e.target.value).toISOString() : "")}
                    style={{
                      width: '100%',
                      padding: '12px 14px',
                      fontSize: '14px',
                      color: '#1e293b',
                      background: '#ffffff',
                      border: '2px solid #e2e8f0',
                      borderRadius: '10px',
                      outline: 'none',
                      transition: 'all 0.2s',
                      boxSizing: 'border-box',
                    }}
                    onFocus={(e) => {
                      e.currentTarget.style.borderColor = '#667eea';
                      e.currentTarget.style.boxShadow = '0 0 0 3px rgba(102, 126, 234, 0.1)';
                    }}
                    onBlur={(e) => {
                      e.currentTarget.style.borderColor = '#e2e8f0';
                      e.currentTarget.style.boxShadow = 'none';
                    }}
                  />
                </div>
              ) : (
                <div style={{
                  background: 'linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%)',
                  border: '1px solid #bae6fd',
                  borderRadius: '12px',
                  padding: '14px 16px',
                }}>
                  <div style={{
                    fontSize: '11px',
                    fontWeight: '600',
                    color: '#0369a1',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    marginBottom: '6px',
                  }}>
                    🕐 When
                  </div>
                  <div style={{
                    fontSize: '14px',
                    fontWeight: '500',
                    color: '#0c4a6e',
                  }}>
                    {formatDateTime(when)}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Where */}
          <div>
            {isEditing ? (
              <div>
                <label style={{
                  display: 'block',
                  fontSize: '12px',
                  fontWeight: '600',
                  color: '#64748b',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  marginBottom: '8px',
                }}>
                  📍 Where
                </label>
                <input
                  type="text"
                  value={where}
                  onChange={(e) => setWhere(e.target.value)}
                  placeholder="Add location"
                  style={{
                    width: '100%',
                    padding: '12px 14px',
                    fontSize: '14px',
                    color: '#1e293b',
                    background: '#ffffff',
                    border: '2px solid #e2e8f0',
                    borderRadius: '10px',
                    outline: 'none',
                    transition: 'all 0.2s',
                    boxSizing: 'border-box',
                  }}
                  onFocus={(e) => {
                    e.currentTarget.style.borderColor = '#667eea';
                    e.currentTarget.style.boxShadow = '0 0 0 3px rgba(102, 126, 234, 0.1)';
                  }}
                  onBlur={(e) => {
                    e.currentTarget.style.borderColor = '#e2e8f0';
                    e.currentTarget.style.boxShadow = 'none';
                  }}
                />
              </div>
            ) : where ? (
              <div style={{
                background: 'linear-gradient(135deg, #fef3c7 0%, #fde68a 100%)',
                border: '1px solid #fcd34d',
                borderRadius: '12px',
                padding: '14px 16px',
              }}>
                <div style={{
                  fontSize: '11px',
                  fontWeight: '600',
                  color: '#92400e',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  marginBottom: '6px',
                }}>
                  📍 Where
                </div>
                <div style={{
                  fontSize: '14px',
                  fontWeight: '500',
                  color: '#78350f',
                }}>
                  {where}
                </div>
              </div>
            ) : null}
          </div>
        </div>

        {/* People - Clean list */}
        {(resolution.contacts.length > 0 || resolution.new_entities?.contacts?.length > 0) && (
          <div style={{ marginBottom: '24px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: '600',
              color: '#64748b',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginBottom: '10px',
            }}>
              👥 People
            </div>
            <div style={{
              background: '#ffffff',
              border: '1px solid #e2e8f0',
              borderRadius: '12px',
              padding: '4px',
            }}>
              {resolution.contacts.map((contact, idx) => (
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = '#f8fafc';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <span style={{
                    fontSize: '14px',
                    fontWeight: '500',
                    color: '#1e293b',
                  }}>{contact.display_name}</span>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '600',
                    color: contact.confidence === 'high' ? '#059669' : '#d97706',
                    background: contact.confidence === 'high' ? '#d1fae5' : '#fed7aa',
                    padding: '4px 10px',
                    borderRadius: '6px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.03em',
                  }}>
                    {contact.confidence === 'high' ? '✓ High' : '~ Medium'}
                  </span>
                </div>
              ))}
              {resolution.new_entities?.contacts?.map((contact, idx) => (
                <div
                  key={`new-${idx}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = '#f8fafc';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'transparent';
                  }}
                >
                  <span style={{
                    fontSize: '14px',
                    fontWeight: '500',
                    color: '#1e293b',
                  }}>{contact.display_name}</span>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '600',
                    color: '#7c3aed',
                    background: '#ede9fe',
                    padding: '4px 10px',
                    borderRadius: '6px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.03em',
                  }}>
                    ✨ New
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* New Places */}
        {resolution.new_entities?.places && resolution.new_entities.places.length > 0 && (
          <div style={{ marginBottom: '24px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: '600',
              color: '#64748b',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginBottom: '10px',
            }}>
              🗺️ New Places
            </div>
            <div style={{
              background: '#ffffff',
              border: '1px solid #e2e8f0',
              borderRadius: '12px',
              padding: '4px',
            }}>
              {resolution.new_entities.places.map((place, idx) => (
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '10px 12px',
                    borderRadius: '8px',
                  }}
                >
                  <span style={{
                    fontSize: '14px',
                    fontWeight: '500',
                    color: '#1e293b',
                  }}>{place.name}</span>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: '600',
                    color: '#7c3aed',
                    background: '#ede9fe',
                    padding: '4px 10px',
                    borderRadius: '6px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.03em',
                  }}>
                    ✨ New
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Relationship Suggestions - Modern cards */}
        {commandData.relationship_suggestions && commandData.relationship_suggestions.length > 0 && (
          <div style={{ marginBottom: '24px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: '600',
              color: '#64748b',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginBottom: '10px',
            }}>
              🔗 Suggested Relationships
            </div>
            <div style={{
              background: 'linear-gradient(135deg, #faf5ff 0%, #f3e8ff 100%)',
              border: '1px solid #e9d5ff',
              borderRadius: '12px',
              padding: '16px',
            }}>
              <p style={{
                fontSize: '13px',
                color: '#6b21a8',
                margin: '0 0 12px 0',
                lineHeight: '1.5',
              }}>
                Based on this event, would you like to create these relationships?
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {commandData.relationship_suggestions.map((suggestion, idx) => (
                  <label
                    key={idx}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: '12px',
                      background: '#ffffff',
                      padding: '14px',
                      borderRadius: '10px',
                      cursor: 'pointer',
                      border: '2px solid transparent',
                      transition: 'all 0.2s',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = '#c084fc';
                      e.currentTarget.style.boxShadow = '0 2px 8px rgba(192, 132, 252, 0.15)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = 'transparent';
                      e.currentTarget.style.boxShadow = 'none';
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedRelationships.has(idx)}
                      onChange={() => toggleRelationship(idx)}
                      style={{
                        width: '18px',
                        height: '18px',
                        marginTop: '2px',
                        cursor: 'pointer',
                        accentColor: '#7c3aed',
                      }}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={{
                        fontSize: '14px',
                        fontWeight: '600',
                        color: '#1e293b',
                        marginBottom: '4px',
                      }}>
                        <span style={{ color: '#7c3aed' }}>{suggestion.from_display_name}</span>
                        {' → '}
                        <span style={{ color: '#7c3aed' }}>{suggestion.to_display_name}</span>
                      </div>
                      <div style={{
                        fontSize: '13px',
                        color: '#64748b',
                        marginBottom: '6px',
                        lineHeight: '1.5',
                      }}>
                        {suggestion.from_display_name} is <strong>{suggestion.relationship_type}</strong> of {suggestion.to_display_name}
                        {suggestion.reciprocal_type && suggestion.reciprocal_type !== suggestion.relationship_type && (
                          <> ({suggestion.to_display_name} is <strong>{suggestion.reciprocal_type}</strong> of {suggestion.from_display_name})</>
                        )}
                      </div>
                      {suggestion.reasoning && (
                        <div style={{
                          fontSize: '12px',
                          color: '#94a3b8',
                          fontStyle: 'italic',
                          marginBottom: '6px',
                          lineHeight: '1.4',
                        }}>
                          {suggestion.reasoning}
                        </div>
                      )}
                      <span style={{
                        display: 'inline-block',
                        fontSize: '11px',
                        fontWeight: '600',
                        padding: '4px 10px',
                        borderRadius: '6px',
                        textTransform: 'uppercase',
                        letterSpacing: '0.03em',
                        background: suggestion.confidence === 'high' ? '#d1fae5' : suggestion.confidence === 'medium' ? '#fed7aa' : '#f1f5f9',
                        color: suggestion.confidence === 'high' ? '#059669' : suggestion.confidence === 'medium' ? '#d97706' : '#64748b',
                      }}>
                        {suggestion.confidence}
                      </span>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Tags */}
        {(tags || isEditing) && (
          <div style={{ marginBottom: '24px' }}>
            <div style={{
              fontSize: '12px',
              fontWeight: '600',
              color: '#64748b',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginBottom: '10px',
            }}>
              🏷️ Tags
            </div>
            {isEditing ? (
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="work, meeting, important"
                style={{
                  width: '100%',
                  padding: '12px 14px',
                  fontSize: '14px',
                  color: '#1e293b',
                  background: '#ffffff',
                  border: '2px solid #e2e8f0',
                  borderRadius: '10px',
                  outline: 'none',
                  transition: 'all 0.2s',
                  boxSizing: 'border-box',
                }}
                onFocus={(e) => {
                  e.currentTarget.style.borderColor = '#667eea';
                  e.currentTarget.style.boxShadow = '0 0 0 3px rgba(102, 126, 234, 0.1)';
                }}
                onBlur={(e) => {
                  e.currentTarget.style.borderColor = '#e2e8f0';
                  e.currentTarget.style.boxShadow = 'none';
                }}
              />
            ) : tags ? (
              <div style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '8px',
              }}>
                {tags.split(",").map((tag, idx) => (
                  <span
                    key={idx}
                    style={{
                      fontSize: '13px',
                      fontWeight: '500',
                      color: '#475569',
                      background: '#f1f5f9',
                      padding: '6px 14px',
                      borderRadius: '8px',
                      border: '1px solid #e2e8f0',
                    }}
                  >
                    {tag.trim()}
                  </span>
                ))}
              </div>
            ) : (
              <p style={{
                fontSize: '14px',
                color: '#94a3b8',
                fontStyle: 'italic',
                margin: 0,
              }}>No tags</p>
            )}
          </div>
        )}

        {/* Event Types */}
        {commandData.extracted.types && commandData.extracted.types.length > 0 && (
          <div>
            <div style={{
              fontSize: '12px',
              fontWeight: '600',
              color: '#64748b',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              marginBottom: '10px',
            }}>
              🔖 Event Types
            </div>
            <div style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: '8px',
            }}>
              {commandData.extracted.types.map((type, idx) => (
                <span
                  key={idx}
                  style={{
                    fontSize: '13px',
                    fontWeight: '500',
                    color: '#1e40af',
                    background: 'linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%)',
                    padding: '6px 14px',
                    borderRadius: '8px',
                    border: '1px solid #93c5fd',
                  }}
                >
                  {type}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Modern Action Buttons */}
      <div style={{
        background: '#f8fafc',
        padding: '20px 28px',
        borderTop: '1px solid #e2e8f0',
        display: 'flex',
        gap: '12px',
      }}>
        <button
          onClick={handleConfirm}
          disabled={isSubmitting || !title.trim()}
          style={{
            flex: 1,
            background: isSubmitting || !title.trim()
              ? '#cbd5e1'
              : 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            color: '#ffffff',
            border: 'none',
            borderRadius: '12px',
            padding: '14px 24px',
            fontSize: '15px',
            fontWeight: '600',
            cursor: isSubmitting || !title.trim() ? 'not-allowed' : 'pointer',
            transition: 'all 0.2s',
            boxShadow: isSubmitting || !title.trim()
              ? 'none'
              : '0 4px 12px rgba(102, 126, 234, 0.3)',
            opacity: isSubmitting || !title.trim() ? 0.6 : 1,
          }}
          onMouseEnter={(e) => {
            if (!isSubmitting && title.trim()) {
              e.currentTarget.style.transform = 'translateY(-1px)';
              e.currentTarget.style.boxShadow = '0 6px 16px rgba(102, 126, 234, 0.4)';
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = isSubmitting || !title.trim()
              ? 'none'
              : '0 4px 12px rgba(102, 126, 234, 0.3)';
          }}
        >
          {isSubmitting ? '✨ Creating...' : '✓ Create Event'}
        </button>
        <button
          onClick={onCancel}
          disabled={isSubmitting}
          style={{
            background: '#ffffff',
            color: '#64748b',
            border: '2px solid #e2e8f0',
            borderRadius: '12px',
            padding: '14px 24px',
            fontSize: '15px',
            fontWeight: '600',
            cursor: isSubmitting ? 'not-allowed' : 'pointer',
            transition: 'all 0.2s',
            opacity: isSubmitting ? 0.5 : 1,
          }}
          onMouseEnter={(e) => {
            if (!isSubmitting) {
              e.currentTarget.style.borderColor = '#cbd5e1';
              e.currentTarget.style.background = '#f8fafc';
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = '#e2e8f0';
            e.currentTarget.style.background = '#ffffff';
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
