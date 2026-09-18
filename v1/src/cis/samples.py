"""Loading the bundled sample call, and the scripted stand-ins for the models.

The demo runs offline. There is no API key, no network call, and no hidden
cost to running `make demo` -- which matters because the thing worth looking
at is the control flow, not the quality of a particular model's extraction.

The scripted extractor below is keyword-matched against the sample transcript.
It is honest about what it is: a stand-in that returns quotes copied from the
text it was given, which is what a well-behaved model does. It also
deliberately fabricates one quote, so the demo's hallucination counter has
something real to report.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Sequence

from .consent.model import (
    Call,
    ConsentLedger,
    ConsentMethod,
    ConsentRecord,
    Participant,
    ParticipantRole,
)
from .extract.grounded import Field
from .ingest.tracks import Attribution, AudioTrack
from .transcript.model import Segment, Transcript, Word

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
DEFAULT_SAMPLE = SAMPLES / "discovery_call.json"

#: Even spacing across the segment. Real word timings come from forced
#: alignment (section 5.3); these exist so the sample has the shape of a real
#: transcript without shipping an audio file.
MS_PER_WORD = 380


def _words(text: str, start_ms: int) -> tuple[Word, ...]:
    out = []
    cursor = start_ms
    for token in text.split():
        out.append(Word(token, cursor, cursor + MS_PER_WORD))
        cursor += MS_PER_WORD
    return tuple(out)


class SampleCall:
    def __init__(self, call: Call, ledger: ConsentLedger, transcript: Transcript):
        self.call = call
        self.ledger = ledger
        self.transcript = transcript

    @property
    def tracks(self) -> tuple[AudioTrack, ...]:
        """Per-speaker tracks, as a platform that supports them would return.

        No mixed recording is offered, which is the point of section 4.2: the
        pipeline is never handed something it could diarize.
        """
        return tuple(
            AudioTrack(
                participant_id=p.participant_id,
                uri=f"s3://sample-audio/{self.call.call_id}/{p.participant_id}.wav",
                attribution=Attribution.PLATFORM_TRACK,
            )
            for p in self.call.participants
        )


def load_sample(path: Path | str = DEFAULT_SAMPLE) -> SampleCall:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    call_raw = raw["call"]
    participants = tuple(
        Participant(
            participant_id=p["participant_id"],
            display_name=p.get("display_name"),
            email=p.get("email"),
            role=ParticipantRole(p.get("role", "unknown")),
        )
        for p in call_raw["participants"]
    )
    call = Call(
        call_id=call_raw["call_id"],
        external_id=call_raw["external_id"],
        participants=participants,
        scheduled_at=dt.datetime.fromisoformat(call_raw["scheduled_at"]),
        source_type=call_raw["source_type"],
        channels=call_raw.get("channels", 1),
    )

    ledger = ConsentLedger(
        ConsentRecord(
            call_id=call.call_id,
            participant_id=r["participant_id"],
            method=ConsentMethod(r["method"]),
            evidence_ref=r.get("evidence_ref"),
            recorded_at=dt.datetime.fromisoformat(r["recorded_at"]),
        )
        for r in raw["consent"]
    )

    transcript_raw = raw["transcript"]
    segments = [
        Segment(
            segment_id=s["segment_id"],
            participant_id=s.get("participant_id"),
            text=s["text"],
            start_ms=s["start_ms"],
            end_ms=s["start_ms"] + max(1, len(s["text"].split())) * MS_PER_WORD,
            words=_words(s["text"], s["start_ms"]),
            attribution="platform",
        )
        for s in transcript_raw["segments"]
    ]
    transcript = Transcript(
        call.call_id,
        segments,
        asr_engine=transcript_raw.get("asr_engine", "unknown"),
        asr_model=transcript_raw.get("asr_model", "unknown"),
    )
    return SampleCall(call, ledger, transcript)


class SamplePlatform:
    """A platform client backed by the sample."""

    def __init__(self, sample: SampleCall):
        self._sample = sample
        self.fetches = 0

    def per_speaker_tracks(self, external_id: str):
        self.fetches += 1
        return self._sample.tracks

    def mixed_track(self, external_id: str):
        self.fetches += 1
        return None


class SampleTranscriber:
    def __init__(self, sample: SampleCall):
        self._sample = sample

    def transcribe(self, tracks, hints):
        return Transcript(
            self._sample.transcript.call_id,
            self._sample.transcript.segments,
            asr_engine=self._sample.transcript.asr_engine,
            asr_model=self._sample.transcript.asr_model,
            vocabulary_hash=hints.hash,
        )


#: (trigger substring, field, value, quote substring, confidence, subject)
#: The quote is a substring of the transcript line, which the pipeline then
#: locates to produce a span. The one exception is marked below.
_SCRIPT: Sequence[tuple[str, str, str, str, float, str | None]] = (
    ("forty hours a week", "metric", "40 hours a week across three people",
     "it takes them about forty hours a week between them", 0.91, "p_dana"),
    ("two hundred thousand", "metric", "$200k ARR lost to missed renewals",
     "roughly two hundred thousand in ARR we had to go and win back", 0.88, "p_dana"),
    ("three hundred percent", "metric", "300% first-year return",
     "Most of our customers see a three hundred percent return", 0.75, "p_rep"),
    ("Manually, which is the problem", "pain", "renewals handled manually by three people",
     "Manually, which is the problem", 0.93, "p_dana"),
    ("Salesforce", "competitor", "Salesforce", "We looked at Salesforce for this last year", 0.9, "p_marco"),
    ("doing nothing", "competitor", "status quo", "doing nothing is still on the table", 0.82, "p_marco"),
    ("signs off on anything", "economic_buyer", "Priya Raman, finance",
     "Priya Raman on the finance side signs off on anything over fifty thousand", 0.89, "p_dana"),
    ("fifty thousand", "budget", "$50k approval threshold",
     "anything over fifty thousand", 0.7, "p_dana"),
    ("eighty thousand", "budget", "$80k", "it's more like eighty thousand", 0.86, "p_dana"),
    ("take it to her myself", "champion", "Dana Chen",
     "I'll take it to her myself once we've seen the security review", 0.84, "p_dana"),
    ("infosec team", "decision_process", "infosec review, then procurement, about six weeks",
     "It goes to our infosec team, then procurement", 0.9, "p_marco"),
    ("end of November", "timeline", "decision by end of November",
     "the decision needs to be made by the end of November", 0.9, "p_dana"),
    ("data residency", "decision_criteria", "data residency, then integration effort, then price",
     "the data residency story, then the integration work, and price after that", 0.87, "p_marco"),
    ("security questionnaire", "commitment", "Send the security questionnaire by Friday",
     "I'll send the security questionnaire by Friday", 0.94, "p_rep"),
)

#: Deliberately not in the transcript. The pipeline discards it and the
#: hallucination counter reports it, which is the behaviour worth seeing in a
#: demo -- a run that reports 0.0% every time teaches nothing about the check.
_FABRICATED = (
    "champion",
    "Marco Diaz",
    "Marco said he would champion this to the exec team",
    0.8,
)


class ScriptedModel:
    """A stand-in extractor. No network, no key, no model."""

    def __init__(self, *, fabricate: bool = True):
        self.fabricate = fabricate
        self.seen: list[str] = []

    def extract(self, chunk_text: str, fields) -> dict:
        self.seen.append(chunk_text)
        wanted = {f.value if isinstance(f, Field) else str(f) for f in fields}
        out: dict = {}

        for trigger, field, value, quote, confidence, subject in _SCRIPT:
            if field not in wanted or trigger not in chunk_text:
                continue
            if field in out:
                continue
            out[field] = {
                "value": value,
                "quotes": [quote],
                "confidence": confidence,
                "subject_participant_id": subject,
            }

        # Once, on the first chunk only. A stand-in that fabricated on
        # every chunk would report a hallucination rate that is an
        # artifact of the stand-in rather than a number worth reading.
        if self.fabricate and "champion" in wanted and len(self.seen) == 1:
            _, value, quote, confidence = _FABRICATED
            if "champion" in out:
                # One real quote and one invented one on the same claim. The
                # invented one is discarded and the claim stands on the
                # evidence that exists, which is what grounding is for.
                out["champion"]["quotes"] = list(out["champion"]["quotes"]) + [quote]
            else:
                out["champion"] = {
                    "value": value,
                    "quotes": [quote],
                    "confidence": confidence,
                }

        return out


class ScriptedJudge:
    """Picks a level from the evidence count. Not a model; not pretending to be.

    Enough to exercise the scoring path. A real judge is an LLM behind the
    same `choose_level(prompt)` boundary, and everything the scoring layer
    guarantees -- the anchors, the refusal of off-scale levels, the absence of
    a prior score in the prompt -- holds either way.
    """

    def __init__(self):
        self.prompts: list[str] = []

    def choose_level(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        pieces = prompt.count("quote: ")
        level = 3 if pieces >= 2 else 2
        return {
            "level": level,
            "rationale": f"{pieces} supporting quote(s) in evidence",
        }
