"use client";

import { useState } from "react";

type ClarificationData = {
  type: "clarification_needed";
  questions: string[];
  partial_extraction: Record<string, unknown>;
  original_message: string;
  clarification_id?: string;
};

type EventClarificationCardProps = {
  clarificationData: ClarificationData;
  onSubmit: (answers: string) => void;
  onCancel: () => void;
};

export function EventClarificationCard({
  clarificationData,
  onSubmit,
  onCancel,
}: EventClarificationCardProps) {
  const [answers, setAnswers] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!answers.trim()) return;

    setIsSubmitting(true);
    try {
      await onSubmit(answers);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="border border-yellow-700 rounded-lg p-4 bg-yellow-900/20 space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-semibold text-white flex items-center gap-2">
            <span className="text-yellow-400">⚠</span>
            More Information Needed
          </h3>
          <p className="text-sm text-gray-400 mt-1">
            Please provide additional details to create this event:
          </p>
        </div>
      </div>

      {/* Questions */}
      <div className="space-y-2">
        {clarificationData.questions.map((question, idx) => (
          <div key={idx} className="flex items-start gap-2 text-sm text-gray-300">
            <span className="text-yellow-400 font-medium">{idx + 1}.</span>
            <span>{question}</span>
          </div>
        ))}
      </div>

      {/* Answer Input */}
      <div>
        <label className="text-xs font-medium text-gray-400 uppercase block mb-2">
          Your Answer
        </label>
        <textarea
          value={answers}
          onChange={(e) => setAnswers(e.target.value)}
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
          rows={3}
          placeholder="Provide the missing information..."
          disabled={isSubmitting}
        />
      </div>

      {/* Actions */}
      <div className="flex gap-2 pt-2">
        <button
          onClick={handleSubmit}
          disabled={!answers.trim() || isSubmitting}
          className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isSubmitting ? "Processing..." : "Submit Details"}
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
