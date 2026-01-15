"use client";

import { useState } from "react";

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

  const { resolution } = commandData;

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

      await onConfirm(commandData.preview_id, modifications);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="border border-gray-700 rounded-lg p-4 bg-gray-800/50 space-y-4">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <h3 className="font-semibold text-white">Event Preview</h3>
          <p className="text-sm text-gray-400 mt-1">{commandData.message}</p>
        </div>
        <button
          onClick={() => setIsEditing(!isEditing)}
          className="text-sm text-blue-400 hover:text-blue-300 transition-colors"
        >
          {isEditing ? "Preview" : "Edit"}
        </button>
      </div>

      {/* Extracted Information */}
      <div className="space-y-3">
        <div>
          <label className="text-xs font-medium text-gray-400 uppercase block mb-1">
            What
          </label>
          {isEditing ? (
            <div className="space-y-2">
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Event title"
              />
              <textarea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
                rows={3}
                placeholder="Event summary"
              />
            </div>
          ) : (
            <>
              <p className="text-white font-medium">{title}</p>
              {summary && summary !== title && (
                <p className="text-sm text-gray-300 mt-1">{summary}</p>
              )}
            </>
          )}
        </div>

        <div>
          <label className="text-xs font-medium text-gray-400 uppercase block mb-1">
            When
          </label>
          {isEditing ? (
            <input
              type="datetime-local"
              value={when ? new Date(when).toISOString().slice(0, 16) : ""}
              onChange={(e) => setWhen(e.target.value ? new Date(e.target.value).toISOString() : "")}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          ) : (
            <p className="text-white">
              {when ? new Date(when).toLocaleString() : "Not specified"}
            </p>
          )}
        </div>

        <div>
          <label className="text-xs font-medium text-gray-400 uppercase block mb-1">
            Where
          </label>
          {isEditing ? (
            <input
              type="text"
              value={where}
              onChange={(e) => setWhere(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Location"
            />
          ) : (
            <p className="text-white">{where || "Not specified"}</p>
          )}
        </div>

        {/* Existing Contacts */}
        {resolution.contacts && resolution.contacts.length > 0 && (
          <div>
            <label className="text-xs font-medium text-gray-400 uppercase">
              People (Found)
            </label>
            <div className="space-y-1 mt-1">
              {resolution.contacts.map((contact, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="text-white">{contact.display_name}</span>
                  <span className="text-xs text-gray-500">
                    {contact.confidence === "high" ? "✓" : "~"}
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
              <label className="text-xs font-medium text-gray-400 uppercase">
                New People (Will Create)
              </label>
              <div className="space-y-1 mt-1">
                {resolution.new_entities.contacts.map((contact, idx) => (
                  <div key={idx} className="text-sm text-yellow-400">
                    {contact.display_name} (new)
                  </div>
                ))}
              </div>
            </div>
          )}

        {/* New Places to Create */}
        {resolution.new_entities?.places &&
          resolution.new_entities.places.length > 0 && (
            <div>
              <label className="text-xs font-medium text-gray-400 uppercase">
                New Places (Will Create)
              </label>
              <div className="space-y-1 mt-1">
                {resolution.new_entities.places.map((place, idx) => (
                  <div key={idx} className="text-sm text-yellow-400">
                    {place.name} (new)
                  </div>
                ))}
              </div>
            </div>
          )}

        <div>
          <label className="text-xs font-medium text-gray-400 uppercase block mb-1">
            Tags
          </label>
          {isEditing ? (
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Comma-separated tags"
            />
          ) : tags ? (
            <div className="flex flex-wrap gap-2 mt-1">
              {tags.split(",").map((tag, idx) => (
                <span
                  key={idx}
                  className="px-2 py-1 text-xs bg-gray-700 text-gray-300 rounded"
                >
                  {tag.trim()}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No tags</p>
          )}
        </div>

        {commandData.extracted.types && commandData.extracted.types.length > 0 && (
          <div>
            <label className="text-xs font-medium text-gray-400 uppercase">
              Event Types
            </label>
            <div className="flex flex-wrap gap-2 mt-1">
              {commandData.extracted.types.map((type, idx) => (
                <span
                  key={idx}
                  className="px-2 py-1 text-xs bg-blue-900/30 text-blue-300 rounded"
                >
                  {type}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-2 pt-2">
        <button
          onClick={handleConfirm}
          disabled={isSubmitting || !title.trim()}
          className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isSubmitting ? "Creating..." : "Confirm & Create Event"}
        </button>
        <button
          onClick={onCancel}
          disabled={isSubmitting}
          className="px-4 py-2 bg-gray-700 text-white rounded-lg hover:bg-gray-600 disabled:opacity-50 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
