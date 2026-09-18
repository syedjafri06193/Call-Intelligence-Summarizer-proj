"""Idempotent writeback keys (design.md section 9.4).

    "Reprocessing a call -- a better model, a bug fix, a retry -- must not
    duplicate anything."

**This module deliberately diverges from the reference implementation in the
design document, because that implementation does not achieve its own stated
goal.** Section 9.4 builds the key like this:

    key = f"call:{call_id}:tv{transcript_version}:ev{extraction_version}"

Now reprocess the call with a better model. `extraction_version` changes, so
the key changes, so `find_by_external_key` misses, so `upsert` takes the
`create` branch -- and the CRM has two notes for one call. The three cases the
section names as the reason for the mechanism are exactly the three cases that
change one of the versions in the key. Only a bare retry, with both versions
unchanged, actually deduplicates.

So the key here identifies the *thing* and not the *run*:

    call:{call_id}:{kind}              -- one summary per call, forever
    call:{call_id}:task:{fingerprint}  -- one task per distinct commitment

and the versions move into the payload as provenance, where they answer the
question they are actually good for: what produced the values currently in
this record. Section 9.4's closing instruction -- "Every downstream object
carries the key" -- is kept, and now means something stable.

The trade is real and worth stating: a stable key means a reprocess
*overwrites* the previous result rather than sitting beside it. That is the
correct default for a CRM field, and `writeback.py` is where the protection
against overwriting a human's edit lives.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class Provenance:
    """What produced the values currently in a record.

    Written as fields alongside the payload rather than baked into the key.
    Same information as the design document's key, used the way it is useful:
    you can see that a record is stale without the record having been
    duplicated to tell you.
    """

    call_id: str
    transcript_version: int
    extractor_version: str
    framework_id: str | None = None
    judge_version: str | None = None

    def as_fields(self) -> Mapping[str, Any]:
        fields: dict[str, Any] = {
            "ai_call_id": self.call_id,
            "ai_transcript_version": self.transcript_version,
            "ai_extractor_version": self.extractor_version,
        }
        if self.framework_id:
            fields["ai_framework"] = self.framework_id
        if self.judge_version:
            fields["ai_judge_version"] = self.judge_version
        return fields


def identity_key(call_id: str, kind: str, discriminator: str | None = None) -> str:
    """The external key for one CRM object.

    Stable across reprocessing, which is the whole point. `kind` is the sort
    of object -- "summary", "note", "task" -- and `discriminator` distinguishes
    several objects of the same kind for one call.
    """
    if not call_id or not kind:
        raise ValueError("call_id and kind are both required for an external key")
    if ":" in kind or ":" in call_id:
        # Without this, identity_key("a:b", "task") and identity_key("a",
        # "b:task") are the same string, and two different objects share a
        # key. Unlikely with opaque platform ids, and free to rule out.
        raise ValueError(
            f"':' is the key separator and may not appear in call_id "
            f"({call_id!r}) or kind ({kind!r})"
        )
    base = f"call:{call_id}:{kind}"
    return f"{base}:{discriminator}" if discriminator else base


def task_fingerprint(title: str) -> str:
    """A stable discriminator for one commitment.

    Normalised before hashing so that a re-extraction producing "Send the
    security questionnaire" where the last run produced "send the security
    questionnaire." updates the same task instead of creating a second one.
    Normalisation is limited to case, whitespace and trailing punctuation:
    anything cleverer starts merging genuinely different commitments, and a
    lost task is worse than a duplicate one.
    """
    normalized = re.sub(r"\s+", " ", title).strip().strip(".!,;:").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class CRM(Protocol):
    """The CRM boundary."""

    def find_by_external_key(self, key: str) -> Any | None:
        ...

    def create(self, payload: Mapping[str, Any], *, external_key: str) -> Any:
        ...

    def update(self, record_id: str, payload: Mapping[str, Any]) -> Any:
        ...

    def get_field(self, record_id: str, field: str) -> str | None:
        ...

    def is_ai_authored(self, record_id: str, field: str) -> bool:
        ...


def upsert(
    crm: CRM,
    key: str,
    payload: Mapping[str, Any],
    *,
    provenance: Provenance | None = None,
) -> Any:
    """Create or update by external key. Never both.

    The `find` then `create` sequence has a race: two workers reprocessing the
    same call can both miss and both create. Real CRMs offer an upsert keyed
    on an external id that resolves this server-side, and this function is the
    single place to swap to it. Doing the check here rather than at each call
    site is what makes that a one-line change.
    """
    body = dict(payload)
    if provenance is not None:
        body.update(provenance.as_fields())
    body["external_key"] = key

    existing = crm.find_by_external_key(key)
    if existing is not None:
        return crm.update(_record_id(existing), body)
    return crm.create(body, external_key=key)


def _record_id(record: Any) -> str:
    if isinstance(record, Mapping):
        return str(record["id"])
    return str(getattr(record, "id"))
