"""Vocabulary biasing (design.md section 5.2).

    "Vocabulary biasing is the highest-value tuning."

    "Feeding the participant list and the account name into the recognizer is
    cheap and fixes the errors that matter most. It's a far better use of
    effort than chasing a point of global WER."

The reasoning from section 5.1 is worth keeping in view: proper nouns are the
worst ASR category and the one that matters most, because a transcript that
gets 92% of words right and mangles the prospect's company name is failing at
the job. Everything downstream inherits that -- an economic buyer extracted as
"Preeya Ramen" is not a claim anyone can act on, and no amount of grounding
fixes it, because the span will faithfully quote the wrong name.

Two things this module adds to the sketch in 5.2:

**A hash.** Section 5.4 stores `vocabulary_hash` on the transcript. A hint
list is an input to the transcript in the same way the model is, and a
transcript you cannot attribute to a hint list is one you cannot reproduce.

**A budget.** Recognisers cap hint lists, and a list over the cap is either
truncated arbitrarily by the vendor or rejected. Truncating here, in a stated
priority order, means the names that matter most survive: the people actually
on the call come before a maintained list of competitors.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from ..consent.model import Call

#: Most hosted recognisers cap the hint list somewhere in the hundreds.
#: Conservative default; override per provider.
DEFAULT_MAX_HINTS = 400


@dataclass(frozen=True)
class VocabularyHints:
    terms: tuple[str, ...]
    truncated: int = 0

    @property
    def hash(self) -> str:
        """Stable digest, order-independent.

        Order-independent because the same set of hints produces the same
        recognition whatever order they arrive in, and a hash that changed
        when a CRM query reordered its results would make every transcript
        look unreproducible.
        """
        h = hashlib.sha256()
        for term in sorted(t.lower() for t in self.terms):
            h.update(term.encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()[:16]

    def __len__(self) -> int:
        return len(self.terms)


class CRMVocabularySource(Protocol):
    """Only what section 5.2 reads.

    Narrow on purpose. Hints are built from CRM data, and a wide interface
    here invites the hint builder to grow into a general CRM reader that
    someone later calls from a path where it does not belong. Four methods,
    all of them things a recogniser can use.
    """

    def account_name(self, call: Call) -> str | None:
        ...

    def contact_names(self, call: Call) -> Sequence[str]:
        ...

    def product_names(self) -> Sequence[str]:
        ...

    def opportunity_names(self, call: Call) -> Sequence[str]:
        ...


def vocabulary_hints(
    call: Call,
    crm: CRMVocabularySource | None = None,
    *,
    competitors: Iterable[str] = (),
    jargon: Iterable[str] = (),
    max_hints: int = DEFAULT_MAX_HINTS,
) -> VocabularyHints:
    """Build the hint list for one call, in priority order.

    Priority matters because the list gets truncated. Participants first:
    their names are the ones a rep will notice being wrong, and they are the
    only ones the pipeline already knows are definitely going to be said.
    """
    ordered: list[str] = []

    # 1. The people actually on the call.
    for participant in call.participants:
        if participant.display_name:
            ordered.append(participant.display_name)
            ordered.extend(participant.display_name.split())

    if crm is not None:
        # 2. The account and its contacts.
        account = crm.account_name(call)
        if account:
            ordered.append(account)
        ordered.extend(crm.contact_names(call))
        # 3. Open opportunities -- deal names get said out loud.
        ordered.extend(crm.opportunity_names(call))
        # 4. Our own product names.
        ordered.extend(crm.product_names())

    # 5. Maintained lists, least call-specific and first to be cut.
    ordered.extend(competitors)
    ordered.extend(jargon)

    deduped = _dedupe(ordered)
    kept = deduped[:max_hints]
    return VocabularyHints(terms=tuple(kept), truncated=len(deduped) - len(kept))


def _dedupe(terms: Iterable[str]) -> list[str]:
    """Order-preserving dedupe, case-insensitive, dropping useless entries.

    Single characters and common words are dropped: a hint list containing
    "the" biases nothing and costs a slot that a company name needed.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in terms:
        term = re.sub(r"\s+", " ", str(raw or "")).strip()
        if len(term) < 2:
            continue
        key = term.lower()
        if key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


_STOPWORDS = frozenset(
    {
        "the", "and", "for", "inc", "llc", "ltd", "co", "corp", "of", "a", "an",
    }
)
