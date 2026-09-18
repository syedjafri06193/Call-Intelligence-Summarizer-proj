"""Shared fixtures."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cis.consent.model import (  # noqa: E402
    Call,
    ConsentLedger,
    ConsentMethod,
    ConsentRecord,
    Participant,
    ParticipantRole,
)
from cis.transcript.model import Segment, Transcript, Word  # noqa: E402

NOW = dt.datetime(2026, 9, 18, 15, 0, tzinfo=dt.timezone.utc)


def words(text: str, start_ms: int, ms_per_word: int = 400) -> tuple[Word, ...]:
    """Word timings for a line of text, evenly spaced.

    Even spacing is fine for tests: what is being exercised is the mapping
    from character ranges to milliseconds, not the realism of the timings.
    """
    out = []
    t = start_ms
    for token in text.split():
        out.append(Word(token, t, t + ms_per_word))
        t += ms_per_word
    return tuple(out)


def segment(
    seg_id: str,
    participant: str | None,
    text: str,
    start_ms: int,
    *,
    attribution: str = "platform",
) -> Segment:
    return Segment(
        segment_id=seg_id,
        participant_id=participant,
        text=text,
        start_ms=start_ms,
        end_ms=start_ms + max(1, len(text.split())) * 400,
        words=words(text, start_ms),
        attribution=attribution,
    )


@pytest.fixture
def participants() -> tuple[Participant, ...]:
    return (
        Participant("p_rep", "Sam Rivera", "sam@vendor.example", ParticipantRole.REP),
        Participant("p_dana", "Dana Chen", "dana@acme.example", ParticipantRole.PROSPECT),
        Participant("p_marco", "Marco Diaz", "marco@acme.example", ParticipantRole.PROSPECT),
    )


@pytest.fixture
def call(participants) -> Call:
    return Call(
        call_id="call_001",
        external_id="zoom_88812345",
        participants=participants,
        scheduled_at=NOW,
        source_type="zoom_cloud",
        channels=3,
    )


@pytest.fixture
def consented_ledger(call) -> ConsentLedger:
    ledger = ConsentLedger()
    for p in call.participants:
        ledger.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id=p.participant_id,
                method=ConsentMethod.PLATFORM_CONSENT,
                evidence_ref=f"zoom_event_{p.participant_id}",
                recorded_at=NOW,
                email=p.email,
            )
        )
    return ledger


@pytest.fixture
def opening_transcript() -> Transcript:
    """A call that opens with an announcement and two affirmative responses."""
    return Transcript(
        "call_001",
        [
            segment("s0", "p_rep", "Hey Dana, Marco, thanks for making the time.", 0),
            segment(
                "s1",
                "p_rep",
                "Before we start, I'm using an AI assistant to take notes and "
                "it's recording this call. Is everyone okay with that?",
                3_000,
            ),
            segment("s2", "p_dana", "Yes, that's fine with me.", 11_000),
            segment("s3", "p_marco", "Sure, go ahead.", 14_000),
            segment("s4", "p_rep", "Great. So tell me about the current process.", 17_000),
        ],
        asr_engine="whisper",
        asr_model="large-v3",
    )
