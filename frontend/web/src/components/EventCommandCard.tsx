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

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-50 to-indigo-50 px-6 py-4 border-b border-gray-200">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className="text-2xl">📅</span>
              <h3 className="text-lg font-semibold text-gray-900">Event Preview</h3>
            </div>
            <p className="text-sm text-gray-600 mt-1">{commandData.message}</p>
          </div>
          <button
            onClick={() => setIsEditing(!isEditing)}
            className="px-3 py-1.5 text-sm font-medium text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded-lg transition-colors"
          >
            {isEditing ? "📖 Preview" : "✏️ Edit"}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="px-6 py-5 space-y-5">
        {/* Title & Summary */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
            <span>📝</span>
            <span>What</span>
          </label>
          {isEditing ? (
            <div className="space-y-2">
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full px-4 py-2.5 bg-white border border-gray-300 rounded-lg text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="Event title"
              />
              <textarea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                className="w-full px-4 py-2.5 bg-white border border-gray-300 rounded-lg text-gray-900 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                rows={3}
                placeholder="Event summary"
              />
            </div>
          ) : (
            <div className="bg-gray-50 rounded-lg p-4">
              <p className="text-gray-900 font-medium">{title}</p>
              {summary && summary !== title && (
                <p className="text-sm text-gray-600 mt-1">{summary}</p>
              )}
            </div>
          )}
        </div>

        {/* When */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
            <span>🕐</span>
            <span>When</span>
          </label>
          {isEditing ? (
            <input
              type="datetime-local"
              value={when ? new Date(when).toISOString().slice(0, 16) : ""}
              onChange={(e) => setWhen(e.target.value ? new Date(e.target.value).toISOString() : "")}
              className="w-full px-4 py-2.5 bg-white border border-gray-300 rounded-lg text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          ) : (
            <div className="bg-gray-50 rounded-lg px-4 py-2.5">
              <p className="text-gray-900">
                {when ? new Date(when).toLocaleString() : "Not specified"}
              </p>
            </div>
          )}
        </div>

        {/* Where */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
            <span>📍</span>
            <span>Where</span>
          </label>
          {isEditing ? (
            <input
              type="text"
              value={where}
              onChange={(e) => setWhere(e.target.value)}
              className="w-full px-4 py-2.5 bg-white border border-gray-300 rounded-lg text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              placeholder="Location"
            />
          ) : (
            <div className="bg-gray-50 rounded-lg px-4 py-2.5">
              <p className="text-gray-900">{where || "Not specified"}</p>
            </div>
          )}
        </div>

        {/* Existing Contacts */}
        {resolution.contacts && resolution.contacts.length > 0 && (
          <div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
              <span>👥</span>
              <span>People (Found)</span>
            </label>
            <div className="bg-green-50 border border-green-200 rounded-lg p-3 space-y-2">
              {resolution.contacts.map((contact, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between"
                >
                  <span className="text-gray-900 text-sm font-medium">{contact.display_name}</span>
                  <span className="text-xs text-green-700 bg-green-100 px-2 py-0.5 rounded-full">
                    {contact.confidence === "high" ? "✓ High confidence" : "~ Medium confidence"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* New Contacts to Create */}
        {resolution.new_entities?.contacts &&
          resolution.new_entities.contacts.length > 0 && (
            <div>
              <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
                <span>👤</span>
                <span>New People (Will Create)</span>
              </label>
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
                {resolution.new_entities.contacts.map((contact, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <span className="text-amber-700 text-sm font-medium">{contact.display_name}</span>
                    <span className="text-xs text-amber-600 bg-amber-100 px-2 py-0.5 rounded-full">New</span>
                  </div>
                ))}
              </div>
            </div>
          )}

        {/* New Places to Create */}
        {resolution.new_entities?.places &&
          resolution.new_entities.places.length > 0 && (
            <div>
              <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
                <span>🗺️</span>
                <span>New Places (Will Create)</span>
              </label>
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-2">
                {resolution.new_entities.places.map((place, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <span className="text-amber-700 text-sm font-medium">{place.name}</span>
                    <span className="text-xs text-amber-600 bg-amber-100 px-2 py-0.5 rounded-full">New</span>
                  </div>
                ))}
              </div>
            </div>
          )}

        {/* Tags */}
        <div>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
            <span>🏷️</span>
            <span>Tags</span>
          </label>
          {isEditing ? (
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="w-full px-4 py-2.5 bg-white border border-gray-300 rounded-lg text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              placeholder="Comma-separated tags"
            />
          ) : tags ? (
            <div className="flex flex-wrap gap-2">
              {tags.split(",").map((tag, idx) => (
                <span
                  key={idx}
                  className="px-3 py-1 text-xs font-medium bg-gray-100 text-gray-700 rounded-full border border-gray-200"
                >
                  {tag.trim()}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-gray-400 text-sm italic">No tags</p>
          )}
        </div>

        {/* Relationship Suggestions */}
        {commandData.relationship_suggestions && commandData.relationship_suggestions.length > 0 && (
          <div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
              <span>🔗</span>
              <span>Suggested Relationships</span>
            </label>
            <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 space-y-3">
              <p className="text-xs text-purple-700 mb-2">
                Based on this event, would you like to create these relationships?
              </p>
              {commandData.relationship_suggestions.map((suggestion, idx) => (
                <label
                  key={idx}
                  className="flex items-start gap-3 cursor-pointer hover:bg-purple-100 p-2 rounded transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={selectedRelationships.has(idx)}
                    onChange={() => toggleRelationship(idx)}
                    className="mt-0.5 w-4 h-4 text-purple-600 rounded focus:ring-2 focus:ring-purple-500"
                  />
                  <div className="flex-1 text-sm">
                    <div className="font-medium text-gray-900">
                      <span className="text-purple-700">{suggestion.from_display_name}</span>
                      {" → "}
                      <span className="text-purple-700">{suggestion.to_display_name}</span>
                    </div>
                    <div className="text-xs text-gray-600 mt-0.5">
                      {suggestion.from_display_name} is {suggestion.relationship_type} of {suggestion.to_display_name}
                      {suggestion.reciprocal_type && suggestion.reciprocal_type !== suggestion.relationship_type && (
                        <> ({suggestion.to_display_name} is {suggestion.reciprocal_type} of {suggestion.from_display_name})</>
                      )}
                    </div>
                    {suggestion.reasoning && (
                      <div className="text-xs text-gray-500 italic mt-1">
                        {suggestion.reasoning}
                      </div>
                    )}
                    <div className="mt-1">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        suggestion.confidence === "high"
                          ? "bg-green-100 text-green-700"
                          : suggestion.confidence === "medium"
                          ? "bg-yellow-100 text-yellow-700"
                          : "bg-gray-100 text-gray-700"
                      }`}>
                        {suggestion.confidence} confidence
                      </span>
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Event Types */}
        {commandData.extracted.types && commandData.extracted.types.length > 0 && (
          <div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 uppercase mb-2">
              <span>🔖</span>
              <span>Event Types</span>
            </label>
            <div className="flex flex-wrap gap-2">
              {commandData.extracted.types.map((type, idx) => (
                <span
                  key={idx}
                  className="px-3 py-1 text-xs font-medium bg-blue-50 text-blue-700 rounded-full border border-blue-200"
                >
                  {type}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="bg-gray-50 px-6 py-4 border-t border-gray-200 flex gap-3">
        <button
          onClick={handleConfirm}
          disabled={isSubmitting || !title.trim()}
          className="flex-1 px-4 py-2.5 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
        >
          {isSubmitting ? "Creating..." : "✓ Confirm & Create Event"}
        </button>
        <button
          onClick={onCancel}
          disabled={isSubmitting}
          className="px-6 py-2.5 bg-white text-gray-700 font-medium rounded-lg hover:bg-gray-100 disabled:opacity-50 border border-gray-300 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
