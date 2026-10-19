# Glossary

| Term | Meaning |
|---|---|
| **All-party consent** | Every participant must consent to recording. CIS's default mode. |
| **Anchor** | Written description of one level on a criterion's ordinal scale. |
| **ASR** | Automatic speech recognition (speech-to-text). |
| **Attribution** | Knowing which participant spoke a segment. In CIS, never from voice. |
| **BANT** | Budget, Authority, Need, Timing — older qualification framework. |
| **BIPA** | Illinois Biometric Information Privacy Act; voiceprints are biometric identifiers. |
| **Ceiling** | Human inter-rater agreement on a criterion — the most a model can be measured against. |
| **Champion** | Prospect-side advocate with influence who will sell internally. |
| **Chunk** | A window of whole speaker turns sent to the extractor in one call. |
| **Claim** | A typed fact extracted from a call (`GroundedClaim`). |
| **Consent gate** | `may_process` — decides whether a call may be fetched at all. |
| **Contradiction** | Two different values for a single-valued field in one call, surfaced to the rep. |
| **Diarization** | Splitting mixed audio by speaker, usually by voice characteristics. Not used. |
| **Economic buyer** | Person with authority to release budget for the purchase. |
| **Evidence** | Claims eligible to support a criterion, with who said them. |
| **Factual criterion** | Criterion humans agree on; may be auto-written. |
| **Framework** | Versioned YAML defining criteria and anchors (`MEDDICC@3`). |
| **Grounding** | Requiring each claim to cite a span that exists in the transcript. |
| **Hallucination rate** | Share of model-offered quotes not found in the transcript. |
| **Idempotency key** | Stable external key so reprocessing updates rather than duplicates CRM objects. |
| **Judge** | Model that picks an anchor level from evidence (`AnchorJudge`). |
| **Judgment criterion** | Criterion humans disagree on; always reviewed by a human. |
| **Krippendorff's alpha** | Agreement statistic handling missing ratings and ordinal data. |
| **Ledger** | Append-only store of consent records. |
| **MEDDICC** | Metrics, Economic buyer, Decision criteria, Decision process, Identify pain, Champion, Competition. |
| **One-party consent** | One participant's consent suffices (some jurisdictions). Needs a named authoriser in CIS. |
| **Ordinal** | Ordered levels without assumed equal spacing semantics beyond order. |
| **Placeholder** | `[PERSON_1]`-style token replacing PII before a model sees the text. |
| **Provenance** | Versions of everything that produced a stored value. |
| **Reconciliation** | Merging per-chunk claims, unioning spans, finding contradictions. |
| **Restoration map** | Local placeholder → original mapping; never serialised. |
| **Roster** | Participant list from the meeting platform / calendar. |
| **Roster fingerprint** | Order-independent digest of participant IDs on a consent decision. |
| **Span** | Citation into a specific transcript version: segment, char range, ms range, text. |
| **Support level** | `explicit` / `implied` — the model's ordinal statement of how directly a quote states a fact. |
| **Two-leg stereo** | Dialer recording with rep and other party on separate channels. |
| **Voiceprint** | Biometric model of a voice. CIS never builds one. |
| **WER** | Word error rate. |
