from __future__ import annotations

import pytest

import voice_profiles
from schemas import (
    ConfirmedSpeakerVoiceObservation,
    SpeakerVoiceConfirmIn,
    SpeakerVoiceMatchIn,
    VoiceSpeakerObservation,
)


def _unit(index: int) -> list[float]:
    vector = [0.0] * voice_profiles.VOICE_EMBEDDING_DIM
    vector[index] = 1.0
    return vector


def test_match_speakers_auto_labels_high_confidence(monkeypatch):
    monkeypatch.setattr(voice_profiles, "_resolve_participant_contact_ids", lambda _participants: set())
    monkeypatch.setattr(
        voice_profiles,
        "_load_voice_profiles",
        lambda: [
            {
                "contact_id": "contact:alice",
                "display_name": "Alice",
                "emails": ["alice@example.com"],
                "centroid": _unit(0),
            },
            {
                "contact_id": "contact:bob",
                "display_name": "Bob",
                "emails": ["bob@example.com"],
                "centroid": _unit(1),
            },
        ],
    )

    result = voice_profiles.match_speakers(
        SpeakerVoiceMatchIn(
            session_id="session-1",
            speaker_observations=[
                VoiceSpeakerObservation(
                    speaker_id="speaker_1",
                    embeddings=[_unit(0), _unit(0)],
                    embedding_model="pyannote_wespeaker_onnx",
                    embedding_dim=voice_profiles.VOICE_EMBEDDING_DIM,
                )
            ],
        )
    )

    assert result.status == "done"
    assert result.assignments[0].action == "auto_label"
    assert result.assignments[0].candidate.contact_id == "contact:alice"


def test_match_speakers_blocks_duplicate_auto_label(monkeypatch):
    monkeypatch.setattr(voice_profiles, "_resolve_participant_contact_ids", lambda _participants: set())
    monkeypatch.setattr(
        voice_profiles,
        "_load_voice_profiles",
        lambda: [
            {
                "contact_id": "contact:alice",
                "display_name": "Alice",
                "emails": ["alice@example.com"],
                "centroid": _unit(0),
            },
            {
                "contact_id": "contact:bob",
                "display_name": "Bob",
                "emails": ["bob@example.com"],
                "centroid": _unit(1),
            },
        ],
    )

    result = voice_profiles.match_speakers(
        SpeakerVoiceMatchIn(
            session_id="session-1",
            speaker_observations=[
                VoiceSpeakerObservation(
                    speaker_id="speaker_1",
                    embeddings=[_unit(0)],
                    embedding_model="pyannote_wespeaker_onnx",
                    embedding_dim=voice_profiles.VOICE_EMBEDDING_DIM,
                ),
                VoiceSpeakerObservation(
                    speaker_id="speaker_2",
                    embeddings=[_unit(0)],
                    embedding_model="pyannote_wespeaker_onnx",
                    embedding_dim=voice_profiles.VOICE_EMBEDDING_DIM,
                ),
            ],
        )
    )

    assert [assignment.action for assignment in result.assignments] == ["suggest", "suggest"]
    assert {assignment.reason for assignment in result.assignments} == {"duplicate_auto_contact"}


def test_rank_candidates_uses_participant_prior_without_hiding_global_profiles():
    query = [0.80, 0.60] + [0.0] * (voice_profiles.VOICE_EMBEDDING_DIM - 2)
    profiles = [
        {
            "contact_id": "contact:participant",
            "display_name": "Participant",
            "emails": ["p@example.com"],
            "centroid": [0.79, 0.61] + [0.0] * (voice_profiles.VOICE_EMBEDDING_DIM - 2),
        },
        {
            "contact_id": "contact:global",
            "display_name": "Global",
            "emails": ["g@example.com"],
            "centroid": [0.80, 0.60] + [0.0] * (voice_profiles.VOICE_EMBEDDING_DIM - 2),
        },
    ]

    candidates = voice_profiles._rank_candidates(
        query,
        profiles,
        participant_contact_ids={"contact:participant"},
    )

    assert [candidate.contact_id for candidate in candidates] == [
        "contact:participant",
        "contact:global",
    ]
    assert candidates[0].is_participant is True


def test_confirm_speaker_profiles_persists_and_updates_profile(monkeypatch):
    persisted = []
    updated = []
    rejected = []

    monkeypatch.setattr(
        voice_profiles,
        "_persist_confirmed_observation",
        lambda session_id, observation, embeddings: persisted.append(
            (session_id, observation.contact_id, len(embeddings))
        ),
    )
    monkeypatch.setattr(
        voice_profiles,
        "_upsert_voice_profile",
        lambda contact_id, embedding_model, embeddings: updated.append(
            (contact_id, embedding_model, len(embeddings))
        ),
    )
    monkeypatch.setattr(
        voice_profiles,
        "_persist_match_event",
        lambda **kwargs: rejected.append(kwargs),
    )

    result = voice_profiles.confirm_speaker_profiles(
        SpeakerVoiceConfirmIn(
            session_id="session-1",
            observations=[
                ConfirmedSpeakerVoiceObservation(
                    speaker_id="speaker_1",
                    contact_id="contact:alice",
                    embeddings=[_unit(0), _unit(0)],
                    embedding_model="pyannote_wespeaker_onnx",
                    embedding_dim=voice_profiles.VOICE_EMBEDDING_DIM,
                )
            ],
            rejected_matches=[
                {
                    "speaker_id": "speaker_2",
                    "suggested_contact_id": "contact:bob",
                    "corrected_contact_id": "contact:alice",
                    "score": 0.8,
                    "margin": 0.02,
                }
            ],
        )
    )

    assert result == {
        "confirmed_observation_count": 2,
        "rejected_match_count": 1,
    }
    assert persisted == [("session-1", "contact:alice", 2)]
    assert updated == [("contact:alice", "pyannote_wespeaker_onnx", 2)]
    assert rejected[0]["status"] == "rejected"


@pytest.mark.parametrize("embedding_dim", [0, 255, 257])
def test_invalid_embedding_dim_does_not_match(monkeypatch, embedding_dim):
    monkeypatch.setattr(voice_profiles, "_resolve_participant_contact_ids", lambda _participants: set())
    monkeypatch.setattr(voice_profiles, "_load_voice_profiles", list)

    result = voice_profiles.match_speakers(
        SpeakerVoiceMatchIn(
            session_id="session-1",
            speaker_observations=[
                VoiceSpeakerObservation(
                    speaker_id="speaker_1",
                    embeddings=[_unit(0)],
                    embedding_model="pyannote_wespeaker_onnx",
                    embedding_dim=embedding_dim,
                )
            ],
        )
    )

    assert result.assignments[0].action == "none"
    assert result.assignments[0].reason == "no_valid_embeddings"
