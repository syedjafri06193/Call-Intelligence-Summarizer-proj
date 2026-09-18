"""Loading a versioned framework definition (design.md section 7.1).

    "Encode it as data -- criteria, definitions, evidence requirements, and
    scoring anchors -- not as prompt text scattered across the codebase."

    "Versioned, because criteria definitions change, and a score must be
    interpretable against the definition in force when it was produced."

The version discipline is the part that is easy to skip and expensive to skip.
A score of 2 on "champion" means nothing on its own; it means something only
against the words that defined level 2 on the day it was produced. So the
framework carries an identity (`name@version`), every score records it, and
the loader refuses a file that does not declare one.

The loader is also deliberately strict about anchors. A scale with a gap in it
is not an ordinal scale, and a level-0 anchor that does not mean "no evidence"
breaks the guarantee in anchors.py that level 0 is assigned by code rather
than by a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from ..extract.grounded import Field


class FrameworkError(ValueError):
    """The framework file is not usable. Raised at load time, on purpose.

    A malformed framework should fail when it is loaded, not when the first
    call is scored against it three hours into a batch.
    """


class CriterionKind(str, Enum):
    """Whether humans agree about this criterion (section 8.1).

    "Agreement is decent on factual criteria (was a metric stated? was a
    decision process described?) and poor on judgment criteria (how strong is
    the champion?). That distribution should shape the product -- auto-write
    the factual criteria, always review the judgment ones."

    This is the field the CRM writeback layer reads to decide what may be
    written without a human looking at it. It is set from measured agreement
    in docs/evaluation.md, not from an opinion about which criteria feel hard.
    """

    FACTUAL = "factual"
    JUDGMENT = "judgment"


#: The anchor that means "we found nothing". Fixed at 0 across every
#: framework, because anchors.py assigns it without consulting a model and
#: that shortcut is only sound if 0 means the same thing everywhere.
NO_EVIDENCE_LEVEL = 0


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    kind: CriterionKind
    definition: str
    evidence_requires: str
    #: level -> written definition of that level. Contiguous from 0.
    anchors: Mapping[int, str]
    #: Extraction fields that can supply evidence for this criterion. The
    #: link between extraction and scoring is declared here rather than
    #: inferred from the name, because section 7.2's `criterion.key in c.field`
    #: sketch only works while the two vocabularies happen to line up --
    #: "identify_pain" and Field.PAIN already do not.
    fields: tuple[Field, ...]
    #: Section 7.1: "Vendor-asserted ROI does not count." When true, evidence
    #: stated by the rep is excluded before scoring rather than weighed down
    #: during it.
    prospect_stated_only: bool = False
    warning: str | None = None

    @property
    def max_level(self) -> int:
        return max(self.anchors)

    @property
    def requires_human_review(self) -> bool:
        return self.kind is CriterionKind.JUDGMENT

    def anchor(self, level: int) -> str:
        try:
            return self.anchors[level]
        except KeyError:
            raise FrameworkError(
                f"{self.key}: level {level} is not on this scale "
                f"(0..{self.max_level})"
            ) from None

    def describe_anchors(self) -> str:
        """The scale as the judge sees it, one line per level."""
        return "\n".join(
            f"  {level}: {self.anchors[level]}" for level in sorted(self.anchors)
        )


@dataclass(frozen=True)
class Framework:
    name: str
    version: int
    criteria: tuple[Criterion, ...]
    notes: str | None = None
    source_path: str | None = None

    @property
    def framework_id(self) -> str:
        """What a stored score points at. `MEDDICC@3`."""
        return f"{self.name}@{self.version}"

    def __getitem__(self, key: str) -> Criterion:
        for criterion in self.criteria:
            if criterion.key == key:
                return criterion
        raise KeyError(key)

    def get(self, key: str) -> Criterion | None:
        try:
            return self[key]
        except KeyError:
            return None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.criteria)

    @property
    def factual(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.kind is CriterionKind.FACTUAL)

    @property
    def judgment(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.kind is CriterionKind.JUDGMENT)

    @property
    def fields_used(self) -> frozenset[Field]:
        """Every extraction field some criterion depends on.

        Useful in the other direction too: a field extracted by nobody's
        criterion is either a missing criterion or a field that should stop
        being extracted.
        """
        return frozenset(f for c in self.criteria for f in c.fields)


def load_framework(path: str | Path) -> Framework:
    """Read and validate a framework YAML file."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise FrameworkError(f"no framework file at {path}") from None
    except yaml.YAMLError as exc:
        raise FrameworkError(f"{path}: not valid YAML: {exc}") from None

    if not isinstance(raw, Mapping):
        raise FrameworkError(f"{path}: expected a mapping at the top level")

    return _build(raw, source_path=str(path))


def _build(raw: Mapping[str, Any], *, source_path: str | None) -> Framework:
    name = raw.get("name")
    version = raw.get("version")
    if not name or not isinstance(name, str):
        raise FrameworkError("framework has no `name`")
    if not isinstance(version, int):
        # A string version sorts lexically and compares wrong, and "3" vs 3 is
        # exactly the sort of thing that silently produces two frameworks that
        # claim to be the same one.
        raise FrameworkError(
            f"{name}: `version` must be an integer, got {version!r}"
        )

    criteria_raw = raw.get("criteria")
    if not isinstance(criteria_raw, Sequence) or not criteria_raw:
        raise FrameworkError(f"{name}: `criteria` must be a non-empty list")

    criteria = tuple(_criterion(name, c) for c in criteria_raw)

    seen: set[str] = set()
    for criterion in criteria:
        if criterion.key in seen:
            raise FrameworkError(f"{name}: duplicate criterion key {criterion.key!r}")
        seen.add(criterion.key)

    return Framework(
        name=name,
        version=version,
        criteria=criteria,
        notes=_text(raw.get("notes")),
        source_path=source_path,
    )


def _criterion(framework_name: str, raw: Any) -> Criterion:
    if not isinstance(raw, Mapping):
        raise FrameworkError(f"{framework_name}: each criterion must be a mapping")

    key = raw.get("key")
    if not key or not isinstance(key, str):
        raise FrameworkError(f"{framework_name}: a criterion has no `key`")

    where = f"{framework_name}.{key}"

    for required in ("label", "definition", "evidence_requires"):
        if not _text(raw.get(required)):
            raise FrameworkError(f"{where}: `{required}` is required")

    kind_raw = raw.get("kind")
    try:
        kind = CriterionKind(kind_raw)
    except ValueError:
        raise FrameworkError(
            f"{where}: `kind` must be 'factual' or 'judgment', got {kind_raw!r}. "
            "It decides whether this criterion can be written to the CRM "
            "without a human looking at it (section 8.1), so it has no default."
        ) from None

    fields = _fields(where, raw.get("fields"))
    anchors = _anchors(where, raw.get("anchors"))

    return Criterion(
        key=key,
        label=str(raw["label"]).strip(),
        kind=kind,
        definition=_text(raw["definition"]) or "",
        evidence_requires=_text(raw["evidence_requires"]) or "",
        anchors=anchors,
        fields=fields,
        prospect_stated_only=bool(raw.get("prospect_stated_only", False)),
        warning=_text(raw.get("warning")),
    )


def _fields(where: str, raw: Any) -> tuple[Field, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise FrameworkError(
            f"{where}: `fields` must be a non-empty list of extraction field "
            "names. A criterion with no fields can never be scored above 0, "
            "which is a silent failure rather than a loud one."
        )
    out: list[Field] = []
    for name in raw:
        try:
            out.append(Field(name))
        except ValueError:
            raise FrameworkError(
                f"{where}: {name!r} is not an extraction field. "
                f"Known fields: {', '.join(sorted(f.value for f in Field))}"
            ) from None
    return tuple(out)


def _anchors(where: str, raw: Any) -> Mapping[int, str]:
    if not isinstance(raw, Mapping) or not raw:
        raise FrameworkError(f"{where}: `anchors` must be a mapping of level to text")

    anchors: dict[int, str] = {}
    for level, text in raw.items():
        if not isinstance(level, int):
            raise FrameworkError(f"{where}: anchor level {level!r} is not an integer")
        body = _text(text)
        if not body:
            raise FrameworkError(f"{where}: anchor {level} has no description")
        anchors[level] = body

    levels = sorted(anchors)
    if levels != list(range(len(levels))):
        # A gap makes the scale non-ordinal: the distance between adjacent
        # levels stops being one step, and every agreement statistic computed
        # on it afterwards is measuring something else.
        raise FrameworkError(
            f"{where}: anchor levels must run contiguously from 0, got {levels}"
        )
    if len(levels) < 2:
        raise FrameworkError(f"{where}: an anchor scale needs at least two levels")

    zero = anchors[NO_EVIDENCE_LEVEL].lower()
    if "not discussed" not in zero and "no evidence" not in zero:
        raise FrameworkError(
            f"{where}: anchor 0 must mean absence -- 'not discussed' or 'no "
            f"evidence'. Got {anchors[NO_EVIDENCE_LEVEL]!r}. Level 0 is "
            "assigned by code without asking a model (score/anchors.py), and "
            "that is only sound while 0 means the same thing everywhere."
        )
    return anchors


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None
