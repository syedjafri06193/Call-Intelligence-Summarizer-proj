"""PII redaction before third-party processing (design.md section 10.2).

    "If transcripts go to a hosted LLM, redact first. Detect and replace,
    keeping a local mapping so you can restore for display."

Three things this module tries hard to be honest about:

**The restoration map never leaves your infrastructure.** That is a sentence in
the design document and a property of a type here: `RestorationMap` has no
serialisation method, and its `repr` shows counts rather than values, so it
cannot be logged by accident. The commonest way a redaction system leaks is
not a missed pattern -- it is the mapping ending up in a log line.

**Names are not detected by pattern.** There is no regex for a name, and an NER
model would be a second model with its own error rate sitting in the sensitive
path. Instead the call roster is used: the participants are known from the
meeting platform, and their names are pseudonymised consistently, which is
what section 10.2 recommends ("consider pseudonymization (consistent
replacement) rather than removal") and what keeps extraction working -- you
need to know that the same person is being discussed.

`roster_names()` expands each display name into the full name *and its parts*,
because transcripts say "Dana", not "Dana Chen". Parts that are also ordinary
English words are dropped: redacting every occurrence of "will", "mark" or
"grace" would remove more business content than PII, and over-redaction
degrades extraction.

A name that is not on the roster is not redacted. That is a real limitation
and it is stated rather than papered over: a prospect naming their CFO who is
not on the call will have that name go to the model.

**Both error directions are measured.** Section 10.2: "PII detection has both
false negatives and false positives, and over-redaction degrades extraction.
Measure both." `RedactionMetrics` reports recall and precision separately,
because they are traded against each other and a single score hides the trade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Iterable, Mapping, Sequence


class PiiKind(str, Enum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CARD = "CARD"
    GOVERNMENT_ID = "GOVID"
    ADDRESS = "ADDRESS"
    PERSON = "PERSON"


@dataclass(frozen=True)
class Detection:
    kind: PiiKind
    start: int
    end: int
    text: str

    @property
    def length(self) -> int:
        return self.end - self.start


class RestorationMap:
    """Placeholder -> original text. Local only.

    Deliberately not a dict and deliberately not serialisable. The mapping is
    the thing that makes redaction reversible, which also makes it exactly as
    sensitive as the transcript it came from. Giving it no `to_json`, no
    `asdict`, and a `repr` that shows counts rather than values removes the
    easy ways for it to end up somewhere it should not be.
    """

    __slots__ = ("_map",)

    def __init__(self, mapping: Mapping[str, str] | None = None) -> None:
        self._map: dict[str, str] = dict(mapping or {})

    def add(self, placeholder: str, original: str) -> None:
        self._map[placeholder] = original

    def original(self, placeholder: str) -> str | None:
        return self._map.get(placeholder)

    def placeholders(self) -> tuple[str, ...]:
        return tuple(sorted(self._map))

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, placeholder: object) -> bool:
        return placeholder in self._map

    def __repr__(self) -> str:
        return f"<RestorationMap {len(self._map)} placeholder(s), contents withheld>"

    __str__ = __repr__


@dataclass
class Redaction:
    """The result of redacting one piece of text."""

    text: str
    restoration: RestorationMap
    detections: tuple[Detection, ...] = ()

    @property
    def counts(self) -> Mapping[PiiKind, int]:
        out: dict[PiiKind, int] = {}
        for detection in self.detections:
            out[detection.kind] = out.get(detection.kind, 0) + 1
        return out


# --------------------------------------------------------------- detectors

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")

#: Deliberately conservative: a run of 7-15 digits with common separators,
#: anchored so it will not fire inside a longer number. Loose phone patterns
#: are the single biggest source of over-redaction, and over-redaction
#: degrades extraction (section 10.2).
_PHONE_RE = re.compile(
    r"(?<![\w-])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?![\w-])"
)

_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

_GOVID_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

#: Case sensitive, and no bare abbreviations. With `re.IGNORECASE` and `dr`
#: in the list, "Our ARR is 2 million dr." is an address, and so is "250 users
#: per drive" -- which removes the budget figure from what the model sees.
#: Street words must be capitalised as they are in a real address, and the
#: ambiguous short forms are only accepted with their trailing period.
_STREET_SUFFIXES = (
    r"Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Lane|Ln\.|"
    r"Drive|Dr\.|Court|Ct\.|Way|Place|Pl\.|Terrace|Parkway|Pkwy\.|Circle"
)
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[A-Z][\w.'-]*\s+){{0,4}}(?:{_STREET_SUFFIXES})(?!\w)"
)


def _luhn(digits: str) -> bool:
    """Check digit validation for card numbers.

    Without it, `_CARD_RE` fires on any long digit run -- an order number, a
    contract reference, an employee count written without separators -- and
    redacts business content the extraction needs. The check costs nothing and
    removes most of that class of false positive.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _find(kind: PiiKind, pattern: re.Pattern[str], text: str) -> list[Detection]:
    out: list[Detection] = []
    for match in pattern.finditer(text):
        matched = match.group(0)
        if kind is PiiKind.CARD:
            digits = re.sub(r"\D", "", matched)
            if not (13 <= len(digits) <= 19) or not _luhn(digits):
                continue
        out.append(Detection(kind, match.start(), match.end(), matched))
    return out


#: First names that are also ordinary words. Redacting these costs more
#: business content than it protects; a fuller list belongs in a data file,
#: and the point here is that the class of problem is handled rather than
#: discovered in production.
_NAME_WORDS_TO_SKIP = frozenset(
    {
        "will", "mark", "grace", "bill", "art", "rose", "may", "june",
        "april", "faith", "hope", "sue", "drew", "chase", "penny", "frank",
        "rich", "don", "jack", "ray", "dawn", "summer", "victor", "max",
    }
)


def roster_names(display_names: Iterable[str | None]) -> tuple[str, ...]:
    """Expand roster display names into what a transcript actually says.

    "Dana Chen" produces both "Dana Chen" and "Dana", because nobody says a
    colleague's surname out loud mid-call. Parts shorter than three characters
    are dropped (initials match everywhere) and so are parts that are ordinary
    English words.
    """
    out: list[str] = []
    for name in display_names:
        if not name or not name.strip():
            continue
        full = " ".join(name.split())
        out.append(full)
        for part in full.split():
            if len(part) < 3 or part.lower() in _NAME_WORDS_TO_SKIP:
                continue
            out.append(part)
    return tuple(dict.fromkeys(out))


def _name_detections(text: str, names: Iterable[str]) -> list[Detection]:
    """Find roster names. Longest first, so "Dana Chen" wins over "Dana"."""
    out: list[Detection] = []
    for name in sorted({n for n in names if n and n.strip()}, key=len, reverse=True):
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        out.extend(
            Detection(PiiKind.PERSON, m.start(), m.end(), m.group(0))
            for m in pattern.finditer(text)
        )
    return out


def _resolve_overlaps(detections: Sequence[Detection]) -> list[Detection]:
    """Keep the longest non-overlapping set, returned earliest first.

    An email contains something a loose phone pattern can match, and an
    address contains digits a card pattern can reach into. Longest-first
    selection means the more specific detector wins -- including when the
    longer match starts *later*, which an earliest-first scan would lose.
    """
    kept: list[Detection] = []
    for detection in sorted(detections, key=lambda d: (-d.length, d.start)):
        if any(detection.start < k.end and k.start < detection.end for k in kept):
            continue
        kept.append(detection)
    return sorted(kept, key=lambda d: d.start)


class Redactor:
    """Consistent, reversible redaction across a whole call.

    One redactor per call, not per line. Consistency is the point: the same
    email address has to become the same placeholder in segment 3 and segment
    91, or the model cannot tell that it is the same address, and section
    10.2's "consistent replacement" stops being true.
    """

    def __init__(self, *, names: Iterable[str] = ()) -> None:
        self._names = tuple(names)
        self._restoration = RestorationMap()
        self._assigned: dict[tuple[PiiKind, str], str] = {}
        self._counters: dict[PiiKind, int] = {}

    @property
    def restoration(self) -> RestorationMap:
        return self._restoration

    def placeholder_for(self, kind: PiiKind, original: str) -> str:
        """Stable placeholder for one exact string.

        Keyed on the text as written, NOT case-folded. Folding would give
        "Dana@Acme.example" and "dana@acme.example" one placeholder, and
        `restore` would then hand back whichever spelling was seen first --
        so a model that quoted faithfully would produce a quote that no longer
        matches the transcript, be recorded as a hallucination, and have its
        claim discarded. Corrupting the headline hallucination metric is a
        worse outcome than the model seeing two placeholders for one address.
        """
        key = (kind, original)
        existing = self._assigned.get(key)
        if existing:
            return existing
        self._counters[kind] = self._counters.get(kind, 0) + 1
        placeholder = f"[{kind.value}_{self._counters[kind]}]"
        self._assigned[key] = placeholder
        self._restoration.add(placeholder, original)
        return placeholder

    def detect(self, text: str) -> list[Detection]:
        found: list[Detection] = []
        found += _find(PiiKind.EMAIL, _EMAIL_RE, text)
        found += _find(PiiKind.GOVERNMENT_ID, _GOVID_RE, text)
        found += _find(PiiKind.CARD, _CARD_RE, text)
        found += _find(PiiKind.PHONE, _PHONE_RE, text)
        found += _find(PiiKind.ADDRESS, _ADDRESS_RE, text)
        found += _name_detections(text, self._names)
        return _resolve_overlaps(found)

    def redact(self, text: str) -> Redaction:
        """Replace detected PII, recording how to put it back."""
        detections = self.detect(text)
        # Numbered in reading order: assign first, substitute afterwards.
        # Doing both in one right-to-left pass numbers the placeholders
        # backwards, which is confusing in a prompt and in a log.
        for detection in detections:
            self.placeholder_for(detection.kind, detection.text)

        out = text
        # Right to left, so earlier offsets stay valid.
        for detection in sorted(detections, key=lambda d: d.start, reverse=True):
            placeholder = self.placeholder_for(detection.kind, detection.text)
            out = out[: detection.start] + placeholder + out[detection.end :]
        return Redaction(
            text=out, restoration=self._restoration, detections=tuple(detections)
        )

    def restore(self, text: str) -> str:
        """Put the originals back, for display to someone entitled to see them.

        Longest placeholder first so `[PERSON_1]` is not mangled by a
        substitution for `[PERSON_11]`.
        """
        out = text
        for placeholder in sorted(self._restoration.placeholders(), key=len, reverse=True):
            original = self._restoration.original(placeholder)
            if original is not None:
                out = out.replace(placeholder, original)
        return out


def redact(text: str, *, names: Iterable[str] = ()) -> tuple[str, RestorationMap]:
    """Section 10.2's signature, for one-shot use.

    Prefer `Redactor` for a whole call: a fresh redactor per line restarts the
    counters and breaks the consistency that makes the placeholders usable.
    """
    redactor = Redactor(names=names)
    result = redactor.redact(text)
    return result.text, result.restoration


# ------------------------------------------------------------- measurement


@dataclass(frozen=True)
class RedactionMetrics:
    """Both error directions, never averaged (section 10.2).

    They pull against each other: tightening a pattern to stop redacting
    order numbers will eventually start missing card numbers. A single
    "accuracy" figure would let one be traded for the other without anyone
    noticing which.
    """

    true_positives: int
    false_positives: int
    false_negatives: int
    by_kind: Mapping[PiiKind, tuple[int, int, int]] = dc_field(default_factory=dict)

    @property
    def recall(self) -> float:
        """Of the PII that was there, how much was redacted?

        The number that matters for the legal commitment.
        """
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 1.0

    @property
    def precision(self) -> float:
        """Of what was redacted, how much was actually PII?

        The number that matters for extraction quality: everything below this
        line was business content that the model never saw.
        """
        flagged = self.true_positives + self.false_positives
        return self.true_positives / flagged if flagged else 1.0

    def report(self) -> str:
        return (
            f"recall     {self.recall:.3f}  (missed {self.false_negatives})\n"
            f"precision  {self.precision:.3f}  (over-redacted {self.false_positives})"
        )


def measure(
    text: str, gold: Sequence[Detection], *, names: Iterable[str] = ()
) -> RedactionMetrics:
    """Score a redactor against hand-labelled PII spans.

    Matching is on overlap rather than exact offsets: a detector that catches
    a phone number but includes the trailing period has not made a mistake
    worth counting as both a miss and a false positive.
    """
    found = Redactor(names=names).detect(text)
    unmatched = list(gold)
    true_positives = 0
    false_positives = 0
    per_kind: dict[PiiKind, list[int]] = {}

    def bump(kind: PiiKind, index: int) -> None:
        per_kind.setdefault(kind, [0, 0, 0])[index] += 1

    for detection in found:
        hit = next(
            (
                g
                for g in unmatched
                if g.kind is detection.kind
                and detection.start < g.end
                and g.start < detection.end
            ),
            None,
        )
        if hit is None:
            false_positives += 1
            bump(detection.kind, 1)
        else:
            unmatched.remove(hit)
            true_positives += 1
            bump(detection.kind, 0)

    for missed in unmatched:
        bump(missed.kind, 2)

    return RedactionMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=len(unmatched),
        by_kind={k: tuple(v) for k, v in per_kind.items()},
    )
