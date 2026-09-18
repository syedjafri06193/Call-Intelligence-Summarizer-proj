"""The CLI and the bundled sample.

A demo that stops working is worse than no demo: it is the first thing anyone
runs, and a broken one says the rest is broken too. These tests run the real
commands against the real sample file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cis.cli import main
from cis.samples import load_sample

ROOT = Path(__file__).resolve().parents[1]


class TestTheSampleIsCoherent:
    def test_it_loads(self):
        sample = load_sample()
        assert sample.call.call_id == "call_demo_001"
        assert len(sample.call.participants) == 3
        assert len(sample.transcript) == 17

    def test_every_participant_has_a_consent_record(self):
        sample = load_sample()
        recorded = {r.participant_id for r in sample.ledger.for_call(sample.call.call_id)}
        assert recorded == {p.participant_id for p in sample.call.participants}

    def test_the_verbal_consents_point_at_real_spans(self):
        # An evidence_ref that names a segment which does not exist is a
        # consent record you cannot substantiate.
        sample = load_sample()
        for record in sample.ledger.for_call(sample.call.call_id):
            if record.evidence_ref and record.evidence_ref.startswith("span:"):
                segment_id = record.evidence_ref.split(":", 1)[1]
                assert sample.transcript.segment(segment_id) is not None

    def test_every_segment_has_word_timings(self):
        sample = load_sample()
        assert all(s.words for s in sample.transcript.segments)

    def test_the_sample_offers_no_mixed_recording(self):
        # Section 4.2: the pipeline is never handed something it could
        # diarize, and the sample should not model a platform that would.
        sample = load_sample()
        assert all(t.participant_id for t in sample.tracks)


class TestDemo:
    def test_it_runs_and_reports_every_stage(self, capsys):
        assert main(["demo"]) == 0
        out = capsys.readouterr().out
        for heading in (
            "consent gate",
            "transcript",
            "redaction",
            "extraction",
            "contradictions",
            "score",
            "proposed tasks",
            "warnings",
        ):
            assert heading in out

    def test_the_demo_shows_a_hallucination_being_caught(self, capsys):
        main(["demo"])
        out = capsys.readouterr().out
        assert "discarded champion" in out
        assert "hallucination rate 3.3%" in out

    def test_the_demo_shows_the_budget_contradiction(self, capsys):
        main(["demo"])
        out = capsys.readouterr().out
        assert "$50k approval threshold" in out
        assert "$80k" in out

    def test_the_demo_shows_vendor_asserted_roi_being_excluded(self, capsys):
        main(["demo"])
        out = capsys.readouterr().out
        assert "300% first-year return" in out
        assert "stated by the rep" in out

    def test_the_demo_proposes_a_task_awaiting_confirmation(self, capsys):
        main(["demo"])
        out = capsys.readouterr().out
        assert "Send the security questionnaire by Friday" in out
        assert "confirm: True" in out

    def test_no_pii_reaches_the_model_in_the_demo(self):
        """Asserted against what the model was handed, not against stdout.

        The earlier version of this test checked the CLI's output, which
        never printed the email in the first place -- it passed with
        redaction switched off entirely.
        """
        from cis.samples import (
            SamplePlatform,
            SampleTranscriber,
            ScriptedJudge,
            ScriptedModel,
            load_sample,
        )
        from cis.score.framework import load_framework
        from cis.workflows.pipeline import run

        sample = load_sample()
        model = ScriptedModel()
        run(
            sample.call,
            sample.ledger,
            SamplePlatform(sample),
            SampleTranscriber(sample),
            model,
            ScriptedJudge(),
            load_framework(ROOT / "docs" / "frameworks" / "meddicc.yaml"),
            now=sample.call.scheduled_at,
        )
        seen = "\n".join(model.seen)
        assert seen, "the model was never called"
        assert "priya.raman@acme.example" not in seen
        assert "415-555-0142" not in seen
        assert "[EMAIL_1]" in seen and "[PHONE_1]" in seen
        # Roster first names are pseudonymised, because that is what a
        # transcript actually says.
        assert "Dana" not in seen
        assert "[PERSON_" in seen


class TestGate:
    def test_a_consented_call_passes(self, capsys):
        assert main(["gate"]) == 0
        assert "allowed:     True" in capsys.readouterr().out

    def test_dropping_a_participant_refuses_with_a_remediation(self, capsys):
        assert main(["gate", "--drop", "p_marco"]) == 2
        out = capsys.readouterr().out
        assert "allowed:     False" in out
        assert "remediation:" in out
        assert "p_marco" in out


class TestFrameworkCommand:
    @pytest.mark.parametrize("name", ["meddicc.yaml", "bant.yaml"])
    def test_both_bundled_frameworks_print(self, name, capsys):
        assert main(["framework", "--framework", str(ROOT / "docs" / "frameworks" / name)]) == 0
        out = capsys.readouterr().out
        assert "auto-writable:" in out
        assert "always review:" in out

    def test_a_broken_framework_reports_rather_than_traces(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: X\nversion: 1\ncriteria: []\n")
        assert main(["framework", "--framework", str(bad)]) == 1
        assert "framework error" in capsys.readouterr().err


class TestAgreementCommand:
    def test_the_bundled_labels_reproduce_the_champion_ceiling(self, capsys):
        labels = ROOT / "eval" / "labeled" / "sample_labels.json"
        assert main(["agreement", str(labels)]) == 0
        out = capsys.readouterr().out
        assert "champion" in out
        assert "0.385" in out
        assert "fix the definition before the model" in out

    def test_the_labels_file_is_well_formed(self):
        labels = json.loads(
            (ROOT / "eval" / "labeled" / "sample_labels.json").read_text()
        )
        assert len(labels) == 10
        for per_rater in labels.values():
            assert set(per_rater) == {"r1", "r2", "r3"}
