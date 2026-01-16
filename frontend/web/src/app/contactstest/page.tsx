'use client';

import { useState } from "react";
import { api } from "@/lib/api";

type ResolvedContact = {
  original_text: string;
  contact_id: string;
  display_name: string;
  matched_via: string;
  confidence: string;
  resolution_path?: string[] | null;
};

type NewContact = {
  original_text: string;
  display_name: string;
  inferred_profession?: string | null;
};

type AmbiguousContact = {
  original_text: string;
  candidates: Array<{
    contact_id: string;
    display_name: string;
    match_score: number;
  }>;
  clarification_prompt: string;
};

type ResolveResponse = {
  status: "success" | "needs_clarification" | "no_people" | "error";
  text: string;
  people_mentioned: string[];
  resolved_contacts: ResolvedContact[];
  new_contacts: NewContact[];
  ambiguous_contacts: AmbiguousContact[];
  message?: string;
};

export default function ContactsTestPage() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<ResolveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResponse(null);

    try {
      const result = await api.post<ResolveResponse>("/contacts/resolve", {
        text: text.trim(),
      });
      setResponse(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container mx-auto p-6 max-w-4xl">
      <h1 className="text-3xl font-bold mb-6">Contacts Resolver Test</h1>

      <form onSubmit={handleSubmit} className="mb-8">
        <div className="mb-4">
          <label htmlFor="text" className="block text-sm font-medium mb-2">
            Enter text with person mentions:
          </label>
          <textarea
            id="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[100px]"
            placeholder="e.g., Had lunch with John Smith and visited my daughter's doctor"
            disabled={loading}
          />
        </div>

        <button
          type="submit"
          disabled={loading || !text.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
        >
          {loading ? "Resolving..." : "Resolve Contacts"}
        </button>
      </form>

      {error && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-md">
          <h3 className="text-red-800 font-semibold mb-1">Error</h3>
          <p className="text-red-700">{error}</p>
        </div>
      )}

      {response && (
        <div className="space-y-6">
          <div className="p-4 bg-gray-50 border border-gray-200 rounded-md">
            <h3 className="font-semibold mb-2">Status</h3>
            <p className="text-sm">
              <span className="font-medium">Status:</span>{" "}
              <span
                className={`inline-block px-2 py-1 rounded text-xs ${
                  response.status === "success"
                    ? "bg-green-100 text-green-800"
                    : response.status === "needs_clarification"
                      ? "bg-yellow-100 text-yellow-800"
                      : response.status === "no_people"
                        ? "bg-gray-100 text-gray-800"
                        : "bg-red-100 text-red-800"
                }`}
              >
                {response.status}
              </span>
            </p>
            {response.message && (
              <p className="text-sm mt-2 text-gray-700">{response.message}</p>
            )}
          </div>

          {response.people_mentioned && response.people_mentioned.length > 0 && (
            <div className="p-4 bg-blue-50 border border-blue-200 rounded-md">
              <h3 className="font-semibold mb-2">People Mentioned</h3>
              <ul className="list-disc list-inside space-y-1">
                {response.people_mentioned.map((person, idx) => (
                  <li key={idx} className="text-sm text-gray-700">
                    {person}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {response.resolved_contacts && response.resolved_contacts.length > 0 && (
            <div className="p-4 bg-green-50 border border-green-200 rounded-md">
              <h3 className="font-semibold mb-3 text-green-800">
                ✓ Resolved Contacts ({response.resolved_contacts.length})
              </h3>
              <div className="space-y-3">
                {response.resolved_contacts.map((contact, idx) => (
                  <div key={idx} className="bg-white p-3 rounded border border-green-300">
                    <p className="text-sm">
                      <span className="font-medium">Original:</span> {contact.original_text}
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Resolved to:</span> {contact.display_name}
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Contact ID:</span>{" "}
                      <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">
                        {contact.contact_id}
                      </code>
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Matched via:</span> {contact.matched_via}
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Confidence:</span>{" "}
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs ${
                          contact.confidence === "high"
                            ? "bg-green-100 text-green-800"
                            : contact.confidence === "medium"
                              ? "bg-yellow-100 text-yellow-800"
                              : "bg-red-100 text-red-800"
                        }`}
                      >
                        {contact.confidence}
                      </span>
                    </p>
                    {contact.resolution_path && contact.resolution_path.length > 0 && (
                      <p className="text-sm">
                        <span className="font-medium">Resolution path:</span>{" "}
                        {contact.resolution_path.join(" → ")}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {response.new_contacts && response.new_contacts.length > 0 && (
            <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-md">
              <h3 className="font-semibold mb-3 text-yellow-800">
                ⚠ New Contacts ({response.new_contacts.length})
              </h3>
              <div className="space-y-3">
                {response.new_contacts.map((contact, idx) => (
                  <div key={idx} className="bg-white p-3 rounded border border-yellow-300">
                    <p className="text-sm">
                      <span className="font-medium">Original:</span> {contact.original_text}
                    </p>
                    <p className="text-sm">
                      <span className="font-medium">Display name:</span> {contact.display_name}
                    </p>
                    {contact.inferred_profession && (
                      <p className="text-sm">
                        <span className="font-medium">Inferred profession:</span>{" "}
                        {contact.inferred_profession}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {response.ambiguous_contacts && response.ambiguous_contacts.length > 0 && (
            <div className="p-4 bg-orange-50 border border-orange-200 rounded-md">
              <h3 className="font-semibold mb-3 text-orange-800">
                ? Ambiguous Contacts ({response.ambiguous_contacts.length})
              </h3>
              <div className="space-y-3">
                {response.ambiguous_contacts.map((contact, idx) => (
                  <div key={idx} className="bg-white p-3 rounded border border-orange-300">
                    <p className="text-sm mb-2">
                      <span className="font-medium">Original:</span> {contact.original_text}
                    </p>
                    <p className="text-sm mb-2 text-orange-700">
                      {contact.clarification_prompt}
                    </p>
                    <div className="mt-2">
                      <p className="text-sm font-medium mb-1">Candidates:</p>
                      <ul className="list-disc list-inside space-y-1 ml-2">
                        {contact.candidates.map((candidate, cIdx) => (
                          <li key={cIdx} className="text-sm text-gray-700">
                            {candidate.display_name} (score: {candidate.match_score})
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <details className="p-4 bg-gray-50 border border-gray-200 rounded-md">
            <summary className="cursor-pointer font-semibold">Raw Response</summary>
            <pre className="mt-2 text-xs overflow-auto p-2 bg-white rounded border">
              {JSON.stringify(response, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}
