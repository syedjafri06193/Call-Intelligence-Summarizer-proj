"""A standing assertion of a legal commitment (design.md sections 2.3, 16.3).

    "tests/test_no_voiceprints.py is not paranoia. It's a standing assertion of
    a legal commitment, and it's the thing that catches a well-meaning
    contributor adding pyannote for 'better speaker labels.'"

BIPA regulates biometric identifiers. The plaintiffs in the Otter litigation
allege voiceprints are built from pitch, cadence, and vocal-tract
characteristics in order to identify individuals across future meetings, and
the court allowed both BIPA claims to proceed. Statutory damages are $1,000
negligent / $5,000 intentional **per voiceprint**, and they are
class-actionable.

Speaker diarization that derives identity from voice characteristics plausibly
creates a biometric identifier. So the commitment is:

    Get speaker identity from per-speaker audio tracks plus calendar metadata.
    Never from voice characteristics. Never persist a voice embedding.

These tests are what make that a property of the repository rather than a
paragraph in a document. They are deliberately blunt: a scan of every import
and a scan for the API surface of the libraries in question, because the
failure they guard against is somebody adding a dependency in good faith to
fix a real problem.

The failure message names the document section, so whoever hits this at 6pm on
a Friday understands in one read why the obvious fix is not available.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

#: Everything that ships. `eval/` is scanned too: an evaluation harness is
#: exactly where someone would reach for a diarization library to "measure
#: attribution accuracy", and it runs against real customer audio.
SCANNED_ROOTS = (SRC, ROOT / "eval")

#: Libraries whose purpose is speaker recognition from voice characteristics.
#: Not an exhaustive list of every such library in existence -- it is the list
#: a contributor would actually reach for, which is what this needs to catch.
FORBIDDEN_MODULES = {
    "pyannote",
    "speechbrain",
    "resemblyzer",
    "nemo.collections.asr.models.label_models",
    "nemo.collections.asr.models.clustering_diarizer",
    "pyAudioAnalysis",
    "diart",
    "simple_diarizer",
    "spectralcluster",
    "deepspeaker",
    "voiceid",
}

#: Call-level markers, for the case where a library is vendored or reached
#: through a wrapper and the import name alone would not catch it.
FORBIDDEN_CALL_PATTERNS = (
    r"\bSpeakerRecognition\b",
    r"\bSpeakerEmbedding\b",
    r"\bEncoderClassifier\b",
    r"\bspeaker_embedding\b",
    r"\bvoice_embedding\b",
    r"\bvoiceprint\b",
    r"\bd_vector\b",
    r"\bx_vector\b",
    r"\bxvector\b",
    r"\bSpeakerDiarization\b",
    r"\bdiarize\w*\s*\(",
)

_FORBIDDEN_CALL_RE = tuple(
    re.compile(p, re.IGNORECASE) for p in FORBIDDEN_CALL_PATTERNS
)

#: Files permitted to name the forbidden terms, because their job is to refuse
#: them. Keeping the allowlist to exactly one file means a second file
#: mentioning voiceprints is a test failure and a conversation.
#:
#: An entry that no longer needs to be here is not harmless: it silently
#: exempts a whole file from the scan. `test_the_allowlist_earns_its_entries`
#: fails on a dead entry for that reason.
ALLOWED_MENTIONS = {
    SRC / "cis" / "ingest" / "tracks.py",
}


def python_files() -> list[Path]:
    return sorted(
        p
        for root in SCANNED_ROOTS
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_scanner_actually_sees_the_source():
    """Guard against a scan that passes because it found nothing to scan.

    An import scanner pointed at an empty directory is a test that always
    passes, which is worse than no test because it reads as reassurance.
    """
    files = python_files()
    assert len(files) >= 10, f"only found {len(files)} source files under {ROOT}"
    for expected in ("consent", "ingest", "extract", "score", "eval"):
        assert any(expected in str(p) for p in files), expected


@pytest.mark.parametrize("path", python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_voice_modelling_imports(path: Path):
    """No module imports a speaker-recognition library."""
    for module in imported_modules(path):
        for forbidden in FORBIDDEN_MODULES:
            if module == forbidden or module.startswith(forbidden + "."):
                pytest.fail(
                    f"{path.relative_to(ROOT)} imports {module!r}, which performs "
                    "voice-characteristic speaker modelling.\n\n"
                    "Speaker identity comes from per-speaker platform tracks or "
                    "stereo channels, never from voice. See docs/legal.md "
                    "section 2.3 -- BIPA exposure is $1,000-$5,000 PER "
                    "VOICEPRINT and class-actionable.\n\n"
                    "If you are here because attribution is missing on some "
                    "calls, the supported answers are in docs/legal.md: "
                    "transcribe unattributed, ask the rep to label the turns, "
                    "or skip the call."
                )


@pytest.mark.parametrize("path", python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_voice_embedding_api_surface(path: Path):
    """No module calls into a voice-embedding API, even a vendored one."""
    if path in ALLOWED_MENTIONS:
        return
    text = path.read_text(encoding="utf-8")
    for pattern in _FORBIDDEN_CALL_RE:
        match = pattern.search(text)
        if match:
            line = text[: match.start()].count("\n") + 1
            pytest.fail(
                f"{path.relative_to(ROOT)}:{line} references "
                f"{match.group(0)!r}, which names a voice-embedding operation.\n"
                "See docs/legal.md section 2.3. If this is a false positive, "
                "add the file to ALLOWED_MENTIONS with a comment explaining "
                "why -- deliberately, in a reviewed diff."
            )


def test_the_allowlist_earns_its_entries():
    """Every allowlisted file must actually contain a forbidden term.

    A dead entry exempts a file from the API-surface scan while looking like
    documentation of a deliberate exception, which is the worst combination.
    """
    for path in ALLOWED_MENTIONS:
        text = path.read_text(encoding="utf-8")
        assert any(
            pattern.search(text) for pattern in _FORBIDDEN_CALL_RE
        ), (
            f"{path.relative_to(ROOT)} is allowlisted but names no forbidden "
            "term. Remove it from ALLOWED_MENTIONS rather than leaving a file "
            "silently exempt from the scan."
        )


def test_the_refusal_path_is_the_one_that_mentions_diarization():
    """The allowlisted files earn it by refusing, not by using.

    `tracks.py` names diarization because its exception message tells you why
    you cannot have it. This asserts that is still what the mention is for --
    an allowlist entry that quietly became a real call site would otherwise be
    invisible.
    """
    tracks = (SRC / "cis" / "ingest" / "tracks.py").read_text(encoding="utf-8")
    assert "NoSpeakerAttribution" in tracks
    assert "legal.md" in tracks
    # The mention appears in a refusal, not an invocation.
    assert "diarization is disabled by policy" in tracks.lower()


def test_no_third_party_voice_dependencies_are_declared():
    """The dependency list is the other place a voiceprint can arrive."""
    root = ROOT
    for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN_MODULES:
            package = forbidden.split(".")[0].lower()
            assert package not in text, (
                f"{name} declares a dependency on {package!r}. "
                "See docs/legal.md section 2.3."
            )


def test_the_commitments_are_written_down_where_they_can_be_found():
    """The code-level commitment and the document must not drift apart.

    A test asserting a legal position is only useful if the position is also
    stated somewhere a lawyer, a customer, or a new engineer would look.
    """
    legal = ROOT / "docs" / "legal.md"
    assert legal.exists(), "docs/legal.md is required -- section 14"
    text = legal.read_text(encoding="utf-8").lower()

    for phrase in (
        "no voiceprints",
        "no training on customer data",
        "bipa",
    ):
        assert phrase in text, f"docs/legal.md does not state {phrase!r}"
