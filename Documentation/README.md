# Call Intelligence Summarizer — Design & Build Guide

**Project:** Transcribes discovery calls, scores them against a qualification framework, and writes structured notes and next steps back to the CRM
**Language:** Python
**Status of this document:** planning + reference

> **This document is not legal advice.** Section 2 describes active litigation and statutory exposure accurately as of September 2026, because the legal constraints determine the architecture and you cannot design this system without understanding them. You need actual counsel before you record a single call. Treat section 2 as a list of questions to bring to a lawyer, not as answers.

---

## Table of contents

1. [Executive summary and scope](#1-executive-summary-and-scope)
2. [The legal gate](#2-the-legal-gate)
3. [Consent architecture](#3-consent-architecture)
4. [Audio ingestion and the diarization decision](#4-audio-ingestion-and-the-diarization-decision)
5. [Transcription](#5-transcription)
6. [Extraction and grounding](#6-extraction-and-grounding)
7. [Scoring against a framework](#7-scoring-against-a-framework)
8. [Evaluation](#8-evaluation)
9. [CRM writeback](#9-crm-writeback)
10. [Privacy, PII, and retention](#10-privacy-pii-and-retention)
11. [Cost model](#11-cost-model)
12. [Adoption](#12-adoption)
13. [Tech stack and setup](#13-tech-stack-and-setup)
14. [Repository layout](#14-repository-layout)
15. [Milestone ladder](#15-milestone-ladder)
16. [Reference implementations](#16-reference-implementations)
17. [Stretch goals](#17-stretch-goals)
18. [References](#18-references)

---

## 1. Executive summary and scope

### The original statement

> Transcribes discovery calls, scores them against a qualification framework, and writes structured notes and next steps back to the CRM.

Five findings shape this, and the first one is unlike anything in a normal software project.

1. **This category is in active class-action litigation, and a federal court ruled on it last month.** On August 13, 2026, Judge Eumi K. Lee in the Northern District of California allowed core claims to proceed in *In re Otter.AI Privacy Litigation* — federal Wiretap Act, California CIPA §§ 631 and 632, and **both Illinois BIPA voiceprint claims**. The court held that Otter is a **third-party eavesdropper** under CIPA § 631 rather than an invited participant, specifically because it independently collects, retains, and uses recordings for its own commercial purposes rather than simply returning a transcript to the host. Companies that *deploy* these tools are being named as co-defendants alongside vendors. See section 2.
2. **Two architectural decisions materially change your legal exposure, and both are also the better engineering choice.** Don't build voiceprints — get speaker identity from per-speaker audio tracks and calendar metadata instead. And don't retain or use customer data for your own purposes. The first avoids BIPA entirely; the second is the line the CIPA ruling turned on. See sections 2.3 and 4.2.
3. **Per-speaker audio tracks eliminate diarization.** Zoom, Meet, and Teams can supply separate audio per participant. Taking them removes the hardest and least reliable step in the pipeline, improves accuracy substantially, and avoids voice-characteristic processing. This is the single highest-leverage technical decision in the project.
4. **You cannot claim scoring accuracy without measuring human agreement first.** MEDDIC and BANT are subjective. If two sales managers agree on a call's score only 60% of the time, a model agreeing 65% of the time is at the ceiling, not underperforming. Build the labeled set with multiple raters and report against that ceiling. See section 8.
5. **Every extracted claim must cite a transcript span.** Grounding is simultaneously the anti-hallucination mechanism, the trust mechanism, and the evaluation mechanism. A model that reports an economic buyer who was never mentioned, with no way to check, destroys the product. See section 6.3.

### Revised project statement

> A consent-gated call intelligence pipeline: per-speaker audio ingestion with no voiceprint extraction, transcription with domain vocabulary biasing, span-grounded structured extraction, qualification scoring evaluated against measured inter-rater agreement, and reviewed CRM writeback that never overwrites human-entered data.

### Explicit non-goals

- **Not real-time coaching.** Live in-call prompting is a different product with much harder latency constraints. Post-call processing in minutes is fine.
- **Not a dialer or meeting platform.** You ingest from Zoom/Meet/Teams/dialer APIs.
- **Not a CRM.** You write into one, carefully.
- **Not speaker recognition.** Deliberately. See 2.3.
- **Not a model trainer.** You do not train on customer audio or transcripts. This is an architectural commitment with legal consequences, not a policy statement.
- **Not multi-language for v1.** Each language roughly doubles the evaluation work.

---

## 2. The legal gate ★

Read this before designing anything. The constraints here are not preferences.

### 2.1 All-party consent states

Federal law is one-party consent. Roughly **12 states require all-party consent** as of 2026: California, Connecticut, Delaware, Florida, Illinois, Maryland, Massachusetts, Montana, New Hampshire, Oregon, Pennsylvania, and Washington.

The list is contested at the edges — some states are all-party only for in-person conversations, some create only civil liability, and a few are genuinely debated. **Verify the current list with counsel rather than trusting any published table, including this one.**

Two things make this harder than a lookup:

**The interstate rule.** *Kearney v. Salomon Smith Barney* (Cal. 2006) is generally read to mean California's law follows a Californian across state lines. In practice, if any participant is in an all-party state, assume the stricter rule applies. This is why every serious product just announces recording on every call.

**BIPA's trigger is residency, not location.** Illinois BIPA exposure attaches the moment a single Illinois resident is on the call, regardless of where they're dialing from. In the Otter litigation the court rejected the extraterritoriality argument at the pleading stage.

Statutory exposure, for scale:

| Statute | Damages |
|---|---|
| CIPA (Cal. Penal Code § 637.2) | $5,000 per violation |
| Federal Wiretap Act / ECPA | Greater of $10,000 per violation or $100/day |
| Illinois BIPA | $1,000 negligent / $5,000 intentional, **per voiceprint** |
| Florida | Criminal exposure up to 5 years |

These are per-violation and class-actionable. A product recording thousands of calls has exposure that compounds fast.

### 2.2 What the Otter ruling actually held

The consolidated case is *In re Otter.AI Privacy Litigation*, No. 5:25-cv-06911 (N.D. Cal.). On August 13, 2026 the court granted Otter's motion to dismiss only in part.

**Survived:** federal Wiretap Act, CIPA § 631, CIPA § 632, both Illinois BIPA claims, unjust enrichment, and the UCL claim.
**Dismissed:** CFAA, CDAFA, the Washington Privacy Act claim, and most common-law privacy claims.

The reasoning that matters architecturally: **the court held Otter is a third-party eavesdropper under § 631, not an invited participant, because it independently collects, retains, and uses recordings for its own commercial purposes rather than simply returning a transcript to the meeting host.**

That distinction is now a legally load-bearing line in your architecture. A system that transcribes and returns results to the customer sits on a different side of it than one that retains a corpus and uses it to improve its own models.

A second finding worth internalizing: **single-host consent is not a defense.** The complaint alleges Otter obtained consent, at most, from the host who added the assistant, not from the other participants. Calendar-invite disclaimers are likewise being treated as insufficient. *Cruz v. Fireflies.AI* (C.D. Ill.) is the parallel bellwether.

And: **deploying companies are being named as co-defendants.** If you build this for internal use, your employer is in the blast radius, not just the vendor. Vendor terms that shift consent responsibility to the customer are exactly how that happens.

This is active litigation. It will develop. Check the current state before relying on any of it.

### 2.3 Do not build voiceprints ★

BIPA regulates "biometric identifiers." The plaintiffs allege Otter builds voiceprints from pitch, cadence, and vocal-tract characteristics in order to identify the same individuals across future meetings, and the court let both BIPA claims proceed.

**Speaker diarization that derives identity from voice characteristics plausibly creates a biometric identifier.** BIPA § 15(b) requires written notice and a written release *before* extraction, plus a published retention and destruction schedule, and Illinois courts have consistently rejected boilerplate "as needed for business purposes" language.

The design response is simple and happens to be better engineering anyway:

> **Get speaker identity from per-speaker audio tracks plus calendar metadata. Never from voice characteristics. Never persist a voice embedding.**

Zoom, Meet, and Teams can provide separate audio per participant, each already associated with an account identity. You get perfect speaker attribution with zero voice modeling. You also skip diarization — the most error-prone stage in the pipeline (§4.2).

Where per-speaker tracks are unavailable (a phone bridge, a single mixed recording), the honest options are: degrade to unattributed transcription, ask the rep to label turns, or decline to process. **Do not quietly fall back to voice-based diarization** — that's exactly the behavior at issue in the litigation.

If a voice embedding is ever computed as an intermediate, it must never be persisted, and you should be able to demonstrate that. "It's ephemeral" is a claim you need logs and code review to support.

### 2.4 The architectural commitments

Four decisions that follow directly, and that should be written into the design doc, the terms, and the code:

| Commitment | Why |
|---|---|
| **No voiceprints, ever** | BIPA. Sidesteps the entire biometric claim category. |
| **No training on customer data** | The "own commercial purposes" language in the CIPA ruling. Also a common DPA prohibition. |
| **Process and return; don't accumulate a corpus** | The invited-participant vs third-party-eavesdropper line |
| **Recording blocked by default until per-participant consent is recorded** | Single-host consent is not a defense |

Consider on-device or customer-tenant processing as a further step. It genuinely removes the interception, cloud-transmission, and third-party-processor vectors at once. Note that the loudest advocates for this framing are vendors selling on-device products, so weigh the argument on its merits — but the merits are real.

### 2.5 Beyond the US

- **GDPR**: recording is processing personal data and needs a lawful basis. Legitimate interest is harder to sustain for recording than for most processing; consent is the usual route. Transcription and scoring are further processing purposes that need their own basis. Add Article 17 deletion rights (§10.4).
- **ePrivacy** applies to communications content specifically.
- **Sub-processors**: sending transcripts to a hosted LLM provider makes that provider a sub-processor. Your customer's DPA may not permit it, and you must disclose it.
- **Canada PIPEDA**, **Australia** (state-level surveillance devices acts), and others each have their own rules.

---

## 3. Consent architecture

Consent is the first thing the system does and the gate on everything else. Model it as first-class state, not a flag.

### 3.1 The model

```python
@dataclass(frozen=True)
class ParticipantConsent:
    call_id: str
    participant_id: str          # from the meeting platform, not from voice
    email: str | None
    jurisdiction_hint: str | None  # self-declared or inferred; never authoritative
    method: Literal[
        "verbal_acknowledged",    # announced and they responded affirmatively
        "platform_consent",       # the meeting platform's own consent prompt
        "written_prior",          # a signed agreement on file
        "declined",
        "unknown",
    ]
    evidence_ref: str            # transcript span, platform event id, or doc id
    recorded_at: datetime
```

Key properties:

- **Per participant, not per call.** The host consenting is not the others consenting.
- **Evidence-backed.** Every consent record points at something verifiable: a platform event, a signed document, or a transcript span containing the acknowledgment.
- **Immutable.** Append-only. Withdrawal is a new record, not an edit.
- **`unknown` is not `consented`.** Default deny.

### 3.2 The gate

```python
def may_process(call: Call, policy: ConsentPolicy) -> ProcessingDecision:
    """Runs BEFORE any audio is fetched, stored, or transcribed."""
    consents = store.consents(call.id)

    unknown = [p for p in call.participants
               if consents.get(p.id, UNKNOWN).method in ("unknown", "declined")]

    if unknown and policy.mode == "all_party":
        return ProcessingDecision(
            allowed=False,
            reason=f"{len(unknown)} participant(s) without recorded consent",
            remediation="announce and capture acknowledgment, or exclude the call",
        )
    ...
```

Two rules that make this real rather than decorative:

**The gate runs before audio is fetched.** Not before transcription, not before storage. If you have downloaded the recording, you have already arguably intercepted it. Fetching is the action to gate.

**Default to all-party mode.** A per-customer setting to relax it should require an explicit acknowledgment from someone with authority, and should be logged. Given the interstate rule and BIPA's residency trigger, the operationally correct default is to treat every call as all-party.

### 3.3 Capturing verbal consent

The practical mechanism most products use: announce at the start and capture the acknowledgment in the transcript.

```
"Before we start — I'm using an AI assistant to take notes and it's
recording this call. Is everyone okay with that?"
```

Then an extraction pass over the opening minutes looks for the announcement and each participant's response, and writes `ParticipantConsent` records with the transcript span as evidence.

This creates a chicken-and-egg problem you must handle deliberately: **you need a transcript to detect consent, but consent gates transcription.** The resolution is a narrowly-scoped consent-detection pass over the first N seconds, under a documented policy, whose output is either a consent record or an immediate deletion of everything. Get counsel's view on this specifically — it's the weakest point in the design and you should not paper over it.

Better, where the platform supports it: use the meeting platform's own consent prompt, which produces a platform-level event you can rely on without processing any audio.

### 3.4 Silence is not consent

If someone doesn't respond to the announcement, that's `unknown`, not `verbal_acknowledged`. Boilerplate in a calendar invite is `unknown`. A participant who joins late and missed the announcement is `unknown`.

Build the UI so a rep can see, before the call, which participants lack consent, and prompt them to ask again.

---

## 4. Audio ingestion and the diarization decision

### 4.1 Sources

| Source | Per-speaker tracks? | Audio quality | Notes |
|---|---|---|---|
| **Zoom cloud recording** | Yes (separate audio files per participant) | 16 kHz+ | Best case. Webhook on recording completion. |
| **Google Meet** | Varies by tier | 16 kHz+ | Check what your tier exposes |
| **Microsoft Teams** | Via Graph API | 16 kHz+ | |
| **Recording bot** (joins as a participant) | Usually mixed | Depends | More universal, more visible, more fragile — and it's the architecture at issue in the Otter litigation |
| **Dialer** (Aircall, RingCentral, etc.) | Often two-leg stereo | **8 kHz narrowband** | Two-leg stereo is effectively per-speaker |
| **Phone bridge / single file** | No | 8 kHz | Worst case |

### 4.2 Per-speaker tracks eliminate diarization ★

Diarization — deciding who spoke when from a mixed recording — is the least reliable stage in any call pipeline. It degrades badly with overlapping speech, and sales calls have a lot of overlap. On a five-person call with cross-talk it can be close to useless.

It's also the stage that creates BIPA exposure, because voice-characteristic-based speaker identification is what the Otter plaintiffs allege constitutes voiceprint extraction.

**Both problems disappear if you take per-speaker audio tracks.** Each track has exactly one speaker, already tied to a platform account identity. You get:

- Perfect speaker attribution
- Better ASR accuracy — no cross-talk in each track
- Speaker role (rep vs prospect) from calendar metadata, trivially
- No voice modeling of any kind

This is the highest-leverage decision in the project, and the legal and technical answers happen to coincide.

```python
def ingest(call: Call) -> list[AudioTrack]:
    tracks = platform.per_speaker_tracks(call.external_id)
    if tracks:
        return [AudioTrack(participant_id=t.participant_id, path=t.path,
                           attribution="platform")   # not derived from voice
                for t in tracks]

    # No per-speaker audio. Do NOT fall back to voice-based diarization.
    if call.channels == 2 and call.source_type == "dialer_two_leg":
        return two_leg_stereo_tracks(call)           # channel = speaker

    raise NoSpeakerAttribution(
        "Mixed single-channel audio. Options: transcribe unattributed, "
        "request manual turn labeling, or skip. Voice-based diarization "
        "is disabled by policy — see docs/legal.md §2.3."
    )
```

The explicit exception, with the policy reference in the message, is the point. It makes the constraint visible to whoever hits it six months from now and is tempted to "just add pyannote."

### 4.3 When you only have mixed audio

Honest options, in order of preference:

1. **Transcribe unattributed.** Useful for keyword search and topic extraction; not usable for "who committed to what."
2. **Ask the rep to label.** A short review UI where they tag turns. Tedious, but accurate and consent-clean.
3. **Skip the call.** Sometimes correct.

Set expectations clearly in the UI. A transcript that silently mis-attributes a commitment is worse than one that says "speaker unknown."

---

## 5. Transcription

### 5.1 Accuracy expectations

Published WER figures are measured on clean read speech and do not transfer.

| Condition | Realistic WER |
|---|---|
| Clean read speech, benchmark audio | 3–5% |
| Good VoIP, single speaker per track, native accent | 6–10% |
| Real sales call, 16 kHz, per-speaker tracks | **10–15%** |
| Mixed audio, cross-talk, accents | **15–25%** |
| 8 kHz telephony, cross-talk | **20–30%** |

**Proper nouns are the worst category and matter the most.** Company names, product names, competitor names, and people's names are out-of-vocabulary, and they're exactly what needs to be accurate for CRM writeback. A transcript that gets 92% of words right but mangles the prospect's company name is failing at the job.

### 5.2 Vocabulary biasing is the highest-value tuning

Most ASR systems accept a vocabulary hint list or custom-vocabulary configuration. Populate it per call from data you already have:

```python
def vocabulary_hints(call: Call, crm: CRM) -> list[str]:
    account = crm.account(call.account_id)
    return dedupe([
        account.name,
        *[c.full_name for c in crm.contacts(account.id)],
        *[p.name for p in crm.products()],          # your own product names
        *COMPETITOR_NAMES,                          # a maintained list
        *INDUSTRY_JARGON,
        *[o.name for o in crm.open_opportunities(account.id)],
    ])
```

Feeding the participant list and the account name into the recognizer is cheap and fixes the errors that matter most. It's a far better use of effort than chasing a point of global WER.

### 5.3 Word-level timestamps are required

Not optional — grounding (§6.3) depends on them. Every extracted claim must point at a character or time range in the transcript, and you can only build that from word-level alignment.

Whisper's native timestamps are segment-level and imprecise; forced alignment (WhisperX or equivalent) gives you word-level. Hosted APIs generally offer word timestamps directly.

### 5.4 Store the transcript immutably

```python
@dataclass(frozen=True)
class Transcript:
    call_id: str
    version: int
    engine: str                  # "whisper-large-v3" etc.
    engine_version: str
    vocabulary_hash: str         # what hints were used
    language: str
    segments: list[Segment]      # speaker_id, start, end, words[]
    created_at: datetime
    content_hash: str
```

Re-transcribing with a better model creates a new version; it never mutates the old one. Extractions and scores reference a specific transcript version, so you can always reproduce what the model saw. Same discipline as pinning rule versions in a pricing engine — and for the same reason.

---

## 6. Extraction and grounding

### 6.1 Structure first, judgment second

Split extraction from scoring. Extraction pulls facts; scoring interprets them. Two stages, separately evaluable.

**Extraction** produces things you can verify against the transcript:
- Participants and their stated roles
- Budget figures, timelines, and dates mentioned
- Named competitors
- Stated problems and pain points
- Explicit commitments ("I'll send the security questionnaire by Friday")
- Named next steps and owners
- Objections raised

**Scoring** (§7) interprets those facts against a framework.

The reason to separate them: extraction errors are checkable and fixable. Scoring disagreements are often legitimate. Conflating them means you can't tell which is broken.

### 6.2 Long transcripts

A 45-minute call is roughly 7,000 words. That fits in context, but **extraction quality degrades for material in the middle of long inputs** — the well-documented "lost in the middle" effect. A single pass over a full transcript will reliably miss things from minute 20.

Chunk with overlap, extract per chunk, then reconcile:

```python
def extract(transcript: Transcript, schema: Schema) -> Extraction:
    chunks = chunk_by_turns(transcript, target_tokens=2500, overlap_turns=3)

    partials = [extract_chunk(c, schema) for c in chunks]

    # Reconcile: dedupe by (type, normalized_value), union the spans,
    # and flag genuine contradictions rather than silently picking one.
    return reconcile(partials)
```

Overlap by conversational turns, not by token count — splitting mid-turn loses the context that makes a statement interpretable.

**Surface contradictions rather than resolving them.** If one chunk says the budget is $50k and another says $80k, that's usually a real thing that happened in the call (the number changed during the conversation), and it's more valuable surfaced than silently collapsed.

### 6.3 Grounding: every claim cites a span ★

This is the most important design decision in the extraction layer.

```python
@dataclass(frozen=True)
class GroundedClaim:
    field: str                   # "economic_buyer"
    value: str                   # "Dana Chen, VP Finance"
    spans: list[TranscriptSpan]  # REQUIRED — never empty
    confidence: float
    extractor_version: str

@dataclass(frozen=True)
class TranscriptSpan:
    transcript_version: int
    segment_id: str
    start_char: int
    end_char: int
    start_ms: int                # for click-to-play
    end_ms: int
    text: str                    # the quoted evidence
```

Enforce it structurally:

```python
def validate(claim: GroundedClaim, transcript: Transcript) -> None:
    if not claim.spans:
        raise UngroundedClaim(f"{claim.field} has no supporting span")
    for span in claim.spans:
        actual = transcript.text_at(span)
        if actual != span.text:
            raise SpanMismatch(f"{claim.field}: span text does not match transcript")
```

A claim with no span is discarded, not shown. This turns hallucination from a silent failure into a caught error.

Grounding pays off three ways:

- **Anti-hallucination.** A fabricated economic buyer has no span to point at. Requiring the model to quote makes fabrication structurally harder and mechanically detectable.
- **Trust.** A rep verifies in two seconds by clicking the quote and hearing the audio. This is what makes the product credible in a way that a confident paragraph never is.
- **Evaluation.** You can check whether the cited span actually supports the claim — a much cheaper labeling task than producing the claim from scratch.

**Build this from the first extraction you write.** Retrofitting grounding means re-prompting, re-validating, and re-labeling everything.

### 6.4 Prompt for quotes, then parse

The reliable pattern is to make the model produce the evidence and derive the structure from it:

```
For each field, output:
  - the exact verbatim quote(s) from the transcript that support it
  - the value you derive from those quotes

If no quote supports a field, output null. Do not infer, do not
generalize from context, do not use outside knowledge. A field with
no supporting quote must be null.
```

Then verify each quote appears verbatim in the transcript before accepting the claim. Fuzzy-match to tolerate minor whitespace differences, but reject anything that isn't substantially present.

The verification step is what makes the prompt instruction binding rather than aspirational.

---

## 7. Scoring against a framework

### 7.1 Pick a framework and make it explicit

MEDDIC, MEDDPICC, BANT, SPICED, or a house framework. Encode it as data — criteria, definitions, evidence requirements, and scoring anchors — not as prompt text scattered across the codebase.

```yaml
# frameworks/meddicc.yaml
version: 3
name: MEDDICC
criteria:
  - key: metrics
    label: Metrics
    definition: >
      Quantified business impact the prospect expects, stated by the
      prospect. Vendor-asserted ROI does not count.
    evidence_requires: "a number or measurable outcome stated by a
                        prospect-side participant"
    anchors:
      0: "not discussed"
      1: "vague benefit mentioned, no quantification"
      2: "a number stated but not tied to a business outcome"
      3: "quantified outcome stated by the prospect and tied to a metric they own"
  - key: economic_buyer
    ...
```

Versioned, because criteria definitions change, and a score must be interpretable against the definition in force when it was produced. Same versioning discipline as transcripts and extractions.

### 7.2 Score from extracted claims, not from raw transcript

```python
def score(criterion: Criterion, claims: list[GroundedClaim]) -> CriterionScore:
    relevant = [c for c in claims if criterion.key in c.field]
    if not relevant:
        return CriterionScore(criterion.key, level=0,
                              rationale="no supporting evidence found",
                              spans=[])
    ...
```

Scoring the extraction rather than the transcript means: the score inherits the extraction's spans, so it's grounded for free; scoring is cheap and rerunnable when criteria change; and when a score is wrong you can tell whether extraction missed the evidence or interpretation misjudged it.

### 7.3 Ordinal anchors, not numbers

**Do not ask a model for "a score out of 10."** A number emitted by a language model is a token sequence, not a measurement. It isn't calibrated, it isn't stable across reruns, and 7 versus 8 carries no reliable meaning.

Ask for the **anchor level** — a small ordinal scale (0–3) where each level has a written definition and an evidence requirement. This is more reliable, more explainable, and it matches how humans actually score these frameworks.

### 7.4 LLM judges have known biases

Worth designing around:

| Bias | Effect | Mitigation |
|---|---|---|
| **Verbosity** | Longer transcripts score higher | Score from extracted claims, not raw length |
| **Position** | Material near the start and end weighted more | Chunked extraction (§6.2) |
| **Self-inconsistency** | Different scores on identical reruns | Fix temperature at 0; measure rerun variance and report it |
| **Leniency drift** | Scores creep upward with vague criteria | Strict anchors with evidence requirements |
| **Sycophancy** | Agreeing with a suggested score | Never include a prior score in the prompt |

**Measure your own self-consistency.** Run the same 50 calls ten times and report the variance. If the model gives different scores on reruns, that number belongs in your evaluation report — and it caps how much any accuracy improvement can mean.

---

## 8. Evaluation ★

### 8.1 Measure human agreement before measuring the model

MEDDIC scoring is subjective. Two experienced sales managers scoring the same call will disagree, and **how much they disagree is the ceiling on what your model can be measured against.**

Build the evaluation set properly:

1. Sample 100–200 calls, stratified across segment, deal stage, rep, and outcome.
2. Have **three** experienced raters score each independently, using the same written anchors.
3. Compute inter-rater agreement — Krippendorff's alpha, or Cohen's/Fleiss' kappa — per criterion.
4. Establish a consensus label through adjudicated discussion, not majority vote.

```python
def rater_agreement(labels: dict[str, dict[str, int]]) -> AgreementReport:
    """labels[call_id][rater_id] -> anchor level.
    Report per criterion: agreement is usually much higher for
    'economic buyer identified' than for 'champion strength'."""
```

Typical outcome: agreement is decent on factual criteria (was a metric stated? was a decision process described?) and poor on judgment criteria (how strong is the champion?). **That distribution should shape the product** — auto-write the factual criteria, always review the judgment ones.

### 8.2 Report against the ceiling

| Criterion | Human κ | Model vs consensus | Model vs ceiling |
|---|---|---|---|
| Metrics | 0.71 | 0.68 | 96% |
| Economic Buyer | 0.79 | 0.74 | 94% |
| Decision Criteria | 0.52 | 0.49 | 94% |
| Champion | **0.38** | 0.36 | 95% |

The Champion row is the point. A raw 0.36 looks bad in isolation. Against a human ceiling of 0.38, the model is performing as well as a person — the criterion itself is barely reliable. **Publishing both columns is the honest framing**, and it tells the product team that "Champion" needs a better definition rather than a better model.

### 8.3 Evaluate extraction separately

Extraction errors are objective, so evaluate them objectively:

| Metric | Question |
|---|---|
| **Precision** | Of claims made, how many are supported by their cited span? |
| **Recall** | Of facts present in the transcript, how many were found? |
| **Span accuracy** | Does the cited span actually support the claim? |
| **Hallucination rate** | Claims with no valid span, or spans not in the transcript |

Span verification is a much cheaper labeling task than full extraction — a rater reads a quote and a claim and answers yes or no. You can label thousands of these quickly, which means extraction can be measured far more precisely than scoring.

### 8.4 Evaluate the ASR separately too

Everything downstream is bounded by the transcript. Maintain a small gold set of hand-corrected transcripts and track:

- Overall WER
- **Proper-noun error rate** — the one that matters (§5.1)
- WER by audio source (Zoom vs dialer vs bridge) — this will differ a lot
- Speaker attribution accuracy where per-speaker tracks are unavailable

---

## 9. CRM writeback

This is where trust is won or lost, permanently.

### 9.1 Never overwrite human-entered data

A model that clobbers a rep's carefully written note has ended the product's credibility for that rep and everyone they talk to.

```python
def write_field(crm: CRM, record_id: str, field: str,
                value: str, claim: GroundedClaim) -> WriteResult:
    current = crm.get_field(record_id, field)

    if current and not is_ai_authored(record_id, field):
        # A human wrote this. Never replace it.
        return WriteResult(
            action="suggested",
            reason="field contains human-entered content",
            suggestion=value,
        )
    ...
```

Three safer patterns:

- **Dedicated AI fields.** `AI_Summary__c`, `AI_Next_Steps__c`. Zero collision risk. Best default.
- **Append with attribution.** Add to notes with a clear marker and a link back to the call.
- **Suggest for review.** Surface in the UI; the rep accepts or edits.

### 9.2 Human-in-the-loop by default

Start with everything as a draft. Graduate fields to auto-write individually, only after measured accuracy justifies it.

```python
@dataclass
class FieldPolicy:
    field: str
    mode: Literal["auto", "review", "suggest_only"]
    min_confidence: float
    requires_span: bool = True
    measured_precision: float | None = None   # from §8.3; gates promotion to auto
```

Nothing goes to `auto` without a precision number behind it. "It seems good" is not a threshold.

### 9.3 Next steps become tasks — carefully

The highest-value output and the highest-risk one, because a hallucinated task is an action taken in the world.

```python
def propose_tasks(claims: list[GroundedClaim]) -> list[ProposedTask]:
    tasks = []
    for c in claims:
        if c.field != "commitment":
            continue
        if not c.spans:
            continue                          # ungrounded → never a task
        if c.confidence < TASK_THRESHOLD:
            continue
        tasks.append(ProposedTask(
            title=c.value,
            due=parse_due(c),
            owner=resolve_owner(c),
            evidence=c.spans,                 # shown in the review UI
            requires_confirmation=True,       # ALWAYS for v1
        ))
    return tasks
```

`requires_confirmation=True` unconditionally for v1. A task auto-assigned to a colleague based on a misheard sentence is a meaningfully bad outcome, and the review step costs the rep five seconds.

Resolve relative dates ("by end of next week") against the call date, not the processing date — a call processed the following Monday would otherwise produce a task due in the past.

### 9.4 Idempotency

Reprocessing a call — a better model, a bug fix, a retry — must not duplicate anything.

```python
def upsert(crm: CRM, call_id: str, transcript_version: int,
           extraction_version: str, payload: Payload) -> None:
    key = f"call:{call_id}:tv{transcript_version}:ev{extraction_version}"
    existing = crm.find_by_external_key(key)
    if existing:
        crm.update(existing.id, payload)     # same key → update, never insert
    else:
        crm.create(payload, external_key=key)
```

Most CRMs support an external ID field for exactly this. Use it. Every downstream object — notes, tasks, custom records — carries the key.

### 9.5 Rate limits and failure

CRM APIs have hard limits (Salesforce daily API call allocations, HubSpot per-second caps). Batch writes, back off on 429s, and queue durably.

**A failed writeback must be visible.** A call that processed successfully but silently failed to write is the worst failure mode: the rep believes their notes are in the CRM and they aren't. Surface failures in the UI and retry with backoff.

---

## 10. Privacy, PII, and retention

### 10.1 What's in a transcript

Names, emails, phone numbers, pricing, contract terms, competitive intelligence, internal org details — and sometimes, unexpectedly, health or financial information a prospect volunteers while explaining their situation.

Treat transcripts as sensitive by default, not as text.

### 10.2 Redaction before third-party processing

If transcripts go to a hosted LLM, redact first. Detect and replace, keeping a local mapping so you can restore for display:

```python
def redact(text: str) -> tuple[str, dict[str, str]]:
    """Returns redacted text and a local-only restoration map.
    The map never leaves your infrastructure."""
```

Detect at minimum: emails, phone numbers, credit card and account numbers, government IDs, and street addresses. Names are harder — you often need them for extraction, so consider pseudonymization (consistent replacement) rather than removal.

Be honest about the limits: PII detection has both false negatives and false positives, and over-redaction degrades extraction. Measure both.

### 10.3 Sub-processors

Using a hosted LLM or ASR provider makes them a sub-processor of your customer's data. That means:

- Disclose them in your DPA
- Check their retention and training terms — many offer a zero-retention or no-training tier, and you should be on it
- Data residency: an EU customer may require EU processing
- Your customer's own DPA with *their* customers may prohibit certain sub-processors

### 10.4 Retention and deletion

- **A published retention schedule.** BIPA requires one for biometric data; you shouldn't have biometric data (§2.3), but the discipline applies to transcripts too.
- **Deletion must be complete.** Audio, transcript, extractions, scores, CRM writebacks, search indexes, logs, and backups. Deleting from a vector index or a log aggregator is meaningfully harder than deleting a database row — design for it up front.
- **Deletion on request** (GDPR Article 17) with a documented SLA.
- **Per-participant deletion.** A single participant may request removal of their data from a call others consented to. Decide how you handle it before someone asks.

### 10.5 Access control

- A rep sees their own calls. A manager sees their team's. Not everyone sees everything.
- **Access to a recording is itself an auditable event.** Log it.
- Deal-sensitive content (pricing discussions, competitive intel) may warrant tighter scoping than general notes.

---

## 11. Cost model

Build this in milestone one. It determines whether the product is viable at scale.

| Component | Unit cost | Per 45-min call |
|---|---|---|
| ASR (hosted) | $0.006–0.02 / min | $0.27–0.90 |
| ASR (self-hosted GPU) | amortized | $0.03–0.10 |
| Extraction (~9k tokens, chunked ≈ 2–3× passes) | varies by model | $0.05–0.25 |
| Scoring | ~2k tokens per criterion set | $0.02–0.08 |
| Storage (audio + transcript, 1 yr) | | $0.01–0.03 |
| **Total (hosted ASR)** | | **$0.35–1.25** |
| **Total (self-hosted ASR)** | | **$0.11–0.45** |

At scale: 100 reps × 4 calls/day × 250 days = **100,000 calls/year**.

| Scenario | Annual |
|---|---|
| Hosted ASR, large model extraction | $35k–125k |
| Self-hosted ASR, smaller extraction model | $11k–45k |

Levers worth knowing:

- **ASR dominates** at hosted prices. Self-hosting Whisper on a single GPU transcribes a 45-minute call in a few minutes and pays for itself quickly at volume.
- **Chunking multiplies extraction cost.** Overlapping chunks mean processing more tokens than the transcript contains.
- **Scoring is cheap** once you score from extracted claims rather than raw transcript (§7.2) — another reason to separate the stages.
- **Reprocessing is a real cost.** Every model upgrade that triggers a backfill costs a full run.

---

## 12. Adoption

### 12.1 The tension at the heart of the product

Reps hate CRM data entry — that's why this exists and why they'll want it. Reps also hate being scored, especially when the score goes to their manager. The same product does both.

How you resolve this determines adoption more than accuracy does.

**Lead with time saved, not with grading.** "Your notes are written" is a gift. "Your call scored 6/10" is a performance review. Same system, opposite reception.

Concretely:
- Default the score to visible to the rep, and aggregate-only to the manager, at least initially
- Frame criteria as coverage, not quality: "Metrics: not discussed" is a useful prompt; "Metrics: 2/10" is a grade
- Let reps correct extractions — and **treat their corrections as evaluation data**, which is the cheapest labeled data you will ever get
- Never surface a score without its evidence spans

### 12.2 Speed matters more than you'd think

A summary that arrives within a few minutes of the call ending gets read while context is fresh. One that arrives the next morning gets ignored. Post-call latency of under ten minutes is a real product requirement even though nothing is technically real-time.

### 12.3 Be conspicuously honest about accuracy

Show confidence. Show the evidence. Make corrections easy and make them stick. A tool that's right 85% of the time and visibly flags its uncertainty is trusted; one that's right 92% of the time and presents everything with equal confidence is not — because the 8% is indistinguishable and users learn to check everything.

---

## 13. Tech stack and setup

### 13.1 Choices

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python | The ASR and ML ecosystem lives here |
| **ASR (self-hosted)** | Whisper large-v3 + forced alignment for word timestamps | Best quality/cost at volume; runs on one GPU |
| **ASR (hosted)** | Deepgram / AssemblyAI / Speechmatics | Faster to start; check retention and training terms |
| **Diarization** | **None.** Per-speaker tracks (§4.2) | Legal and technical reasons coincide |
| **LLM** | Hosted, with structured output / tool use | Use a zero-retention tier |
| **Orchestration** | Temporal, or Celery + Redis | Multi-step, long-running, retryable, with durable state |
| **Storage** | S3 (audio, encrypted at rest) + Postgres (structured) | |
| **Search** | Postgres full-text first | Add a vector store only when semantic search is a proven need — deletion is harder there (§10.4) |
| **CRM** | `simple-salesforce` / HubSpot SDK | |
| **Meeting platforms** | Zoom / Graph / Meet APIs, webhook-driven | |

### 13.2 Pipeline as a durable workflow

Every step can fail, and reprocessing must be safe. Temporal (or equivalent) gives you retries, durable state, and visibility for free:

```python
@workflow.defn
class ProcessCall:
    @workflow.run
    async def run(self, call_id: str) -> None:
        decision = await workflow.execute_activity(check_consent, call_id)
        if not decision.allowed:
            await workflow.execute_activity(record_skipped, call_id, decision.reason)
            return                                    # ← gate before fetching audio

        tracks     = await workflow.execute_activity(fetch_audio, call_id)
        transcript = await workflow.execute_activity(transcribe, tracks)
        redacted   = await workflow.execute_activity(redact, transcript)
        claims     = await workflow.execute_activity(extract, redacted)
        scores     = await workflow.execute_activity(score, claims)
        await workflow.execute_activity(stage_for_review, call_id, claims, scores)
```

Note the ordering: the consent gate returns *before* `fetch_audio`. That ordering is the legal control, and it's worth a comment in the code saying so.

---

## 14. Repository layout

```
Call-Intelligence-Summarizer/
├── README.md
├── docs/
│   ├── design.md                ← this document
│   ├── legal.md                 ← ★ consent policy, the §2.4 commitments
│   ├── frameworks/              ← versioned MEDDICC/BANT definitions
│   └── evaluation.md            ← agreement ceilings + model results
├── src/
│   ├── consent/
│   │   ├── model.py
│   │   ├── gate.py              ← ★ runs before audio fetch
│   │   └── detect.py            ← verbal acknowledgment extraction
│   ├── ingest/
│   │   ├── zoom.py
│   │   ├── teams.py
│   │   ├── dialer.py
│   │   └── tracks.py            ← per-speaker; NO voice diarization
│   ├── asr/
│   │   ├── whisper.py
│   │   ├── align.py             ← word-level timestamps
│   │   └── vocabulary.py        ← per-call hints from CRM
│   ├── extract/
│   │   ├── chunk.py
│   │   ├── grounded.py          ← ★ span validation
│   │   ├── reconcile.py
│   │   └── schema.py
│   ├── score/
│   │   ├── framework.py         ← loads versioned YAML
│   │   └── anchors.py
│   ├── redact/
│   ├── crm/
│   │   ├── writeback.py         ← never overwrites human content
│   │   ├── tasks.py
│   │   └── idempotency.py
│   └── workflows/
├── eval/
│   ├── gold/                    ← hand-corrected transcripts
│   ├── labeled/                 ← multi-rater scores
│   ├── agreement.py             ← ★ the ceiling
│   ├── extraction_metrics.py
│   └── consistency.py           ← rerun variance
└── tests/
    ├── test_consent_gate.py     ← ★ the most important test file
    └── test_no_voiceprints.py   ← asserts no embedding is ever persisted
```

`tests/test_no_voiceprints.py` is not paranoia. It's a standing assertion of a legal commitment, and it's the thing that catches a well-meaning contributor adding pyannote for "better speaker labels."

---

## 15. Milestone ladder

### M0 — Legal and consent design ★ **before writing any pipeline code**
**Est. 1 week, plus counsel time**

Write `docs/legal.md`: which jurisdictions, which consent model, the §2.4 architectural commitments, the retention schedule, the sub-processor list. **Review it with a lawyer.** Decide the per-speaker-track requirement and the no-voiceprint rule.

**Done when:** counsel has reviewed it and the consent gate's behavior is specified in writing.

Every other milestone depends on this and none of them are safe without it.

---

### M1 — Consent gate and ingestion
**Est. 1.5 weeks**

The consent model, the gate, per-speaker track fetching, and the explicit failure when tracks are unavailable.

**Done when:** a call with any participant lacking consent is refused *before* audio is fetched, and there is no code path that performs voice-based diarization.

---

### M2 — Transcription
**Est. 1.5 weeks**

ASR, forced alignment for word timestamps, vocabulary biasing from CRM, immutable versioned storage. Plus the gold set and WER harness.

**Done when:** you have WER and proper-noun error rate measured per audio source, not assumed.

---

### M3 — Grounded extraction ★
**Est. 2 weeks**

Chunking with turn overlap, span validation, reconciliation with contradiction surfacing.

**Done when:** a claim without a valid, verbatim span cannot be produced — enforced by the type system and a test, not by convention.

---

### M4 — The evaluation set ★ **before the scoring model**
**Est. 2 weeks, mostly rater time**

150 calls, three raters each, agreement computed per criterion.

**Build this before the scorer.** Otherwise you'll tune against a number that has no known ceiling and can't tell improvement from noise.

**Done when:** `docs/evaluation.md` has a per-criterion human agreement table.

---

### M5 — Scoring
**Est. 1.5 weeks**

Versioned framework YAML, anchor-based scoring from claims, self-consistency measurement.

**Done when:** scores are reported against the human ceiling, and rerun variance is a published number.

---

### M6 — Review UI
**Est. 2 weeks**

Transcript with click-to-play, claims with their evidence spans highlighted, scores with rationale, edit-and-accept.

Corrections here are training and evaluation data. Capture them structurally from day one.

---

### M7 — CRM writeback
**Est. 1.5 weeks**

Dedicated fields, never-overwrite logic, idempotency keys, task proposals with mandatory confirmation, visible failure handling.

**Done when:** reprocessing a call twenty times produces exactly one set of notes and tasks.

---

### M8 — Privacy and retention
**Est. 1.5 weeks**

Redaction, retention schedule enforcement, complete deletion including indexes and logs, access logging.

**Done when:** a deletion request provably removes every artifact, verified by a test that searches every store.

---

### M9 — Scale and cost
**Est. 1 week**

Self-hosted ASR if the volume justifies it, batching, cost per call as a tracked metric.

---

## 16. Reference implementations

### 16.1 Span-validated extraction

```python
def extract_grounded(chunk: Chunk, schema: Schema, llm: LLM) -> list[GroundedClaim]:
    raw = llm.structured(
        system=GROUNDED_EXTRACTION_PROMPT,
        user=chunk.text,
        schema=schema.json_schema,
        temperature=0.0,
    )

    claims: list[GroundedClaim] = []
    for field, payload in raw.items():
        if payload is None:
            continue

        spans = []
        for quote in payload.get("quotes", []):
            located = chunk.locate(quote)      # fuzzy on whitespace, strict on content
            if located is None:
                logger.warning("quote not found in transcript — discarding",
                               field=field, quote=quote[:80])
                continue
            spans.append(located)

        if not spans:
            continue                            # ungrounded → dropped, not shown

        claims.append(GroundedClaim(
            field=field,
            value=payload["value"],
            spans=spans,
            confidence=payload.get("confidence", 0.5),
            extractor_version=EXTRACTOR_VERSION,
        ))
    return claims
```

The `logger.warning` on a discarded quote is your hallucination monitor. Track its rate over time — a rising rate after a model change is a regression signal you'd otherwise miss entirely.

### 16.2 Inter-rater agreement

```python
def krippendorff_alpha(ratings: dict[str, dict[str, int]], levels: int) -> float:
    """ratings[item_id][rater_id] -> anchor level.
    Handles missing ratings, which matters because raters skip calls."""
```

Report it per criterion, not overall. The aggregate hides exactly the variation that should drive product decisions.

### 16.3 The no-voiceprint test

```python
def test_no_voice_embeddings_persisted():
    """A standing assertion of a legal commitment (docs/legal.md §2.3)."""
    forbidden = {"pyannote", "speechbrain", "resemblyzer", "nemo.collections.asr.models.label_models"}
    for module in walk_imports("src/"):
        assert not any(f in module for f in forbidden), (
            f"{module} performs voice-characteristic speaker modeling. "
            "See docs/legal.md §2.3 — BIPA voiceprint exposure."
        )

def test_speaker_attribution_comes_from_platform():
    tracks = ingest(mixed_single_channel_call())
    # Must raise, not silently diarize.
    ...
```

---

## 17. Stretch goals

| Feature | Effort | Value |
|---|---|---|
| **Coaching on the rep side** | Medium | "You asked 3 discovery questions; top performers ask 11." Framed as help, not grading. |
| **Talk ratio and question analysis** | Small | Cheap from per-speaker tracks; genuinely useful |
| **Objection library** | Medium | Cluster objections across calls, surface the best responses |
| **Competitor mention tracking** | Small | High value to product marketing, easy from extraction |
| **Deal-risk signals** | Medium | Feeds naturally into a pipeline forecasting model |
| **Multi-language** | Large | Each language roughly doubles the evaluation work |
| **Call search with citations** | Medium | Semantic search — but note the deletion complexity in §10.4 |
| **Follow-up email drafting** | Small | Grounded in the commitments already extracted |
| **On-device / customer-tenant processing** | Large | Removes the third-party-processor vector entirely (§2.4) |
| **Real-time in-call prompts** | Large | A different product with much harder constraints |

---

## 18. References

### Legal

> Again: this is a reading list, not advice. Get counsel.

| Source | For |
|---|---|
| *In re Otter.AI Privacy Litigation*, No. 5:25-cv-06911 (N.D. Cal.), Aug. 13, 2026 order | The third-party-eavesdropper holding; which claims survived |
| *Cruz v. Fireflies.AI* (C.D. Ill.) | The parallel BIPA bellwether |
| Cal. Penal Code §§ 630–637.2 (CIPA) | All-party consent, $5,000/violation |
| 740 ILCS 14 (BIPA), especially § 15(b) | Written notice and release before voiceprint extraction; retention schedule |
| 18 U.S.C. §§ 2510–2523 (Wiretap Act / ECPA) | Federal baseline |
| *Kearney v. Salomon Smith Barney*, 39 Cal. 4th 95 (2006) | The interstate rule |
| GDPR Arts. 6, 9, 17, 28 + ePrivacy Directive | EU lawful basis, deletion, processors |

### Technical

- **Whisper** (Radford et al., 2022) — and its known failure modes, including hallucinated text on silence
- **WhisperX** — forced alignment for word-level timestamps
- **"Lost in the Middle"** (Liu et al., 2023) — why §6.2 chunks
- **Krippendorff**, *Content Analysis* — the agreement statistics in §8.1
- **"Judging LLM-as-a-Judge"** (Zheng et al., 2023) — position and verbosity bias
- NIST and academic work on ASR evaluation — why benchmark WER doesn't transfer to real audio

### Domain

- MEDDIC / MEDDPICC and BANT primary sources, for writing anchors that mean something
- Zoom, Microsoft Graph, and Google Meet recording APIs — specifically what per-speaker audio each exposes
- Salesforce and HubSpot API docs on external IDs (§9.4) and rate limits

---

## Appendix A — Decision record

| Decision | Rationale |
|---|---|
| **Legal design before any pipeline code** | A federal court allowed Wiretap, CIPA, and BIPA claims to proceed against a product in this exact category on Aug 13, 2026. Deploying companies are co-defendants. |
| **No voiceprints, ever** | BIPA § 15(b) requires written release before extraction; damages are $1,000–$5,000 *per voiceprint*, and residency alone triggers it |
| **Speaker identity from per-speaker tracks + calendar, never voice** | Avoids BIPA *and* eliminates diarization, the least reliable pipeline stage. Legal and technical answers coincide. |
| **Explicit failure, never silent fallback to diarization** | The silent fallback is precisely the behavior at issue in the litigation |
| **No training on customer data; process and return** | The CIPA ruling turned on Otter retaining and using recordings for its own commercial purposes rather than returning a transcript to the host |
| **Consent gate runs before audio fetch** | Fetching is arguably the interception. Gating transcription is too late. |
| Per-participant consent, evidence-backed, default deny | Single-host consent and calendar disclaimers are not a reliable defense |
| Default to all-party mode everywhere | The interstate rule plus BIPA's residency trigger make per-call jurisdiction analysis unreliable |
| **Vocabulary biasing from CRM per call** | Proper nouns are the worst error category and the one that matters most for writeback |
| Word-level timestamps required | Grounding depends on them |
| Immutable versioned transcripts | Extractions and scores must reference exactly what the model saw |
| **Extraction separated from scoring** | Extraction errors are objective and fixable; scoring disagreements are often legitimate. Conflating them means you can't tell which is broken. |
| Chunk with turn overlap, surface contradictions | "Lost in the middle" means single-pass extraction misses minute 20; a changed budget figure is signal, not noise |
| **Every claim cites a verbatim span; ungrounded claims are dropped** | Anti-hallucination, trust, and evaluability in one mechanism |
| Ordinal anchors, not "score out of 10" | A number from an LLM is a token sequence, not a measurement |
| **Measure human agreement before model accuracy** | If raters agree at κ=0.38 on Champion, a model at 0.36 is at the ceiling, not failing |
| Report model performance against the ceiling | Tells the product team when the criterion needs a better definition rather than a better model |
| Measure and publish rerun variance | Self-inconsistency caps what any accuracy improvement can mean |
| **Never overwrite human-entered CRM data** | One clobbered note ends the product's credibility for that rep permanently |
| Dedicated AI fields as the default | Zero collision risk |
| Human-in-the-loop until a measured precision justifies auto-write, per field | "It seems good" is not a threshold |
| Task creation always requires confirmation in v1 | A hallucinated task is an action taken in the world |
| Resolve relative dates against the call date | A Monday reprocess would otherwise create tasks due in the past |
| External-ID idempotency on every written object | Reprocessing must never duplicate |
| Visible writeback failures | Silently failing to write is the worst failure: the rep believes the notes are there |
| Redact before third-party processing; zero-retention LLM tier | Transcripts contain pricing, competitive intel, and volunteered sensitive information |
| Postgres FTS before a vector store | Deletion from a vector index is meaningfully harder (§10.4) |
| **Lead with time saved, not with scoring** | The same system is a gift or a performance review depending entirely on framing |
| Rep corrections captured as evaluation data | The cheapest labeled data you will ever get |
| `test_no_voiceprints.py` as a standing test | Catches the well-meaning contributor who adds pyannote for "better labels" |

---

## Appendix B — Quick reference card

```
LEGAL — read docs/legal.md before touching the pipeline
  In re Otter.AI (N.D. Cal.), Aug 13 2026: Wiretap, CIPA §631/§632,
    BIPA ×2, unjust enrichment, UCL all SURVIVED dismissal
  Court: an AI notetaker that retains + uses recordings for its OWN
    purposes is a third-party eavesdropper, not an invited participant
  Employers who DEPLOY are named as co-defendants
  Single-host consent ≠ all-party consent. Calendar disclaimers ≠ consent.

  All-party states (~12, verify with counsel):
    CA CT DE FL IL MD MA MT NH OR PA WA
  Kearney (2006): CA law follows a Californian across state lines
  BIPA triggers on RESIDENCY — one Illinois resident on the call is enough

  Damages:  CIPA $5,000/violation · ECPA max($10k, $100/day)
            BIPA $1,000 negligent / $5,000 intentional PER VOICEPRINT
            FL criminal up to 5 years

FOUR COMMITMENTS
  1 no voiceprints, ever
  2 no training on customer data
  3 process and return; don't accumulate a corpus
  4 recording blocked by default until per-participant consent recorded

PIPELINE ORDER (the gate position is the legal control)
  consent gate → FETCH AUDIO → transcribe → redact
  → extract (grounded) → score → stage for review → CRM
  ↑ gate returns BEFORE fetch. Gating transcription is too late.

SPEAKER ATTRIBUTION
  per-speaker tracks (Zoom/Teams/Meet) → identity from platform  ✓
  two-leg stereo dialer → channel = speaker                      ✓
  mixed single channel → RAISE. transcribe unattributed, ask the
    rep to label, or skip. Never fall back to voice diarization.

ASR REALITY
  clean benchmark    3–5% WER
  real call, tracks  10–15%
  mixed, 8 kHz       20–30%
  proper nouns are the worst AND the ones that matter
  → vocabulary hints from CRM: account, contacts, products, competitors
  → word-level timestamps are REQUIRED (grounding depends on them)

GROUNDING
  every claim carries ≥1 verbatim span, validated against the transcript
  no span → dropped, never displayed
  log discarded quotes — the rate IS your hallucination monitor

SCORING
  ordinal anchors (0–3) with written definitions, never "x/10"
  score from extracted claims, not raw transcript (kills verbosity bias)
  temperature 0, and measure rerun variance anyway

EVALUATION — ceiling first
  3 raters × 150 calls → κ per criterion
  report model vs consensus AND model vs ceiling
  κ=0.38 human / 0.36 model = at ceiling, not failing

CRM
  never overwrite human-entered fields
  dedicated AI_ fields by default
  external key = call:{id}:tv{n}:ev{v}  → reprocess never duplicates
  tasks ALWAYS require confirmation in v1
  relative dates resolve against the CALL date
  failed writes must be VISIBLE
```
