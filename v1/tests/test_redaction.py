"""PII redaction (design.md section 10.2).

Two things these tests are really checking, beyond "does the regex fire":

**Over-redaction is a failure too.** "Over-redaction degrades extraction" is
in the design document and it is easy to treat as a caveat rather than a test.
A detector that redacts every long number also redacts contract values and
headcounts, and the model then cannot extract the budget. So the false
positives have their own tests.

**The restoration map is the leak risk.** Not the missed pattern -- the
mapping ending up in a log line. Hence the tests on `repr`.
"""

from __future__ import annotations

import pytest

from cis.redact.pii import (
    Detection,
    PiiKind,
    RestorationMap,
    Redactor,
    measure,
    redact,
)


class TestDetection:
    def test_emails_and_phones_and_cards(self):
        text = (
            "Email dana@acme.example or call 415-555-0142. "
            "The card on file is 4242 4242 4242 4242."
        )
        out, _ = redact(text)
        assert "dana@acme.example" not in out
        assert "415-555-0142" not in out
        assert "4242 4242 4242 4242" not in out
        assert "[EMAIL_1]" in out and "[PHONE_1]" in out and "[CARD_1]" in out

    def test_government_ids_and_addresses(self):
        out, _ = redact("SSN 123-45-6789, office at 1600 Pennsylvania Avenue.")
        assert "123-45-6789" not in out
        assert "1600 Pennsylvania Avenue" not in out

    def test_roster_names_are_pseudonymised_consistently(self):
        redactor = Redactor(names=["Dana Chen", "Marco Diaz"])
        first = redactor.redact("Dana Chen owns the budget.")
        second = redactor.redact("I'll follow up with Dana Chen on Friday.")
        # Consistent replacement, not removal: the model still knows these two
        # sentences are about the same person, which extraction needs.
        assert "[PERSON_1]" in first.text
        assert "[PERSON_1]" in second.text
        assert "Dana" not in second.text

    def test_the_longer_name_wins(self):
        redactor = Redactor(names=["Dana", "Dana Chen"])
        out = redactor.redact("Ask Dana Chen.")
        assert out.text == "Ask [PERSON_1]."

    def test_a_name_not_on_the_roster_is_not_detected(self):
        """A stated limitation, asserted so it stays stated.

        There is no regex for a name. A prospect naming their CFO who is not
        on the call will have that name reach the model, and pretending
        otherwise in a DPA would be worse than saying so.
        """
        redactor = Redactor(names=["Dana Chen"])
        out = redactor.redact("Priya Raman signs off on this.")
        assert "Priya Raman" in out.text


class TestOverRedactionIsAlsoAFailure:
    def test_a_long_order_number_is_not_a_card(self):
        # Fails Luhn, so the check that costs nothing removes it.
        out, mapping = redact("Our order reference is 1234567890123456.")
        assert "1234567890123456" in out
        assert len(mapping) == 0

    def test_a_headcount_is_not_a_phone_number(self):
        out, _ = redact("We have 4500 employees and 320 in support.")
        assert "4500" in out and "320" in out

    def test_a_contract_value_survives(self):
        # The budget is the thing extraction most needs. Redacting it would
        # produce a pipeline that is compliant and useless.
        out, _ = redact("The renewal is 250,000 a year.")
        assert "250,000" in out

    def test_a_date_is_not_a_government_id(self):
        out, _ = redact("We signed on 2026-09-18.")
        assert "2026-09-18" in out


class TestRestoration:
    def test_redaction_round_trips(self):
        redactor = Redactor(names=["Dana Chen"])
        original = "Dana Chen at dana@acme.example, or 415-555-0142."
        redacted = redactor.redact(original)
        assert redactor.restore(redacted.text) == original

    def test_double_digit_placeholders_restore_correctly(self):
        redactor = Redactor()
        text = " ".join(f"user{i}@acme.example" for i in range(12))
        redacted = redactor.redact(text)
        assert "[EMAIL_11]" in redacted.text
        assert redactor.restore(redacted.text) == text

    def test_the_map_does_not_print_its_contents(self):
        # The commonest leak is not a missed pattern, it is the mapping in a
        # log line.
        _, mapping = redact("dana@acme.example")
        assert "dana@acme.example" not in repr(mapping)
        assert "dana@acme.example" not in str(mapping)
        assert "contents withheld" in repr(mapping)

    def test_the_map_has_no_serialisation_method(self):
        for attribute in ("to_json", "asdict", "as_dict", "json", "items", "values"):
            assert not hasattr(RestorationMap(), attribute), attribute

    def test_a_fresh_redactor_per_line_would_break_consistency(self):
        # Documented in the module and asserted here, because the one-shot
        # helper is the tempting thing to reach for in a loop.
        first, _ = redact("dana@acme.example")
        second, _ = redact("someone.else@acme.example")
        assert first == second == "[EMAIL_1]"


class TestMeasurement:
    def test_both_error_directions_are_reported(self):
        text = "Call 415-555-0142 or write to dana@acme.example about PO 1234567890123452."
        gold = [
            Detection(PiiKind.PHONE, text.index("415"), text.index("415") + 12, "415-555-0142"),
            Detection(
                PiiKind.EMAIL,
                text.index("dana@"),
                text.index("dana@") + len("dana@acme.example"),
                "dana@acme.example",
            ),
        ]
        metrics = measure(text, gold)
        assert metrics.recall == pytest.approx(1.0)
        # That PO number passes Luhn, so it is redacted and it should not be:
        # a real false positive, and it is counted as one rather than hidden.
        assert metrics.false_positives == 1
        assert metrics.precision == pytest.approx(2 / 3)

    def test_a_near_miss_offset_is_not_counted_twice(self):
        text = "Reach me on 415-555-0142."
        gold = [Detection(PiiKind.PHONE, 12, 25, "415-555-0142.")]
        metrics = measure(text, gold)
        assert (metrics.true_positives, metrics.false_positives, metrics.false_negatives) == (
            1,
            0,
            0,
        )

    def test_recall_and_precision_are_not_averaged(self):
        from cis.redact.pii import RedactionMetrics

        assert not hasattr(RedactionMetrics(1, 1, 1), "accuracy")
        assert not hasattr(RedactionMetrics(1, 1, 1), "f1")


class TestRestoreIsAnExactInverse:
    def test_two_casings_of_one_value_do_not_corrupt_the_round_trip(self):
        """Case-folded placeholder keys break grounding, quietly.

        With one placeholder for both spellings, `restore` hands back whichever
        was seen first, the model's faithful quote stops matching the
        transcript, it is counted as a hallucination and the claim is
        discarded. Corrupting the headline hallucination metric is worse than
        two placeholders for one address.
        """
        redactor = Redactor()
        text = "Email Dana@Acme.example first, then dana@acme.example."
        redacted = redactor.redact(text)
        assert redactor.restore(redacted.text) == text
        assert "[EMAIL_1]" in redacted.text and "[EMAIL_2]" in redacted.text

    def test_placeholders_are_numbered_in_reading_order(self):
        redactor = Redactor()
        out = redactor.redact("Mail alpha@x.example or beta@y.example")
        assert out.text.index("[EMAIL_1]") < out.text.index("[EMAIL_2]")
        assert redactor.restoration.original("[EMAIL_1]") == "alpha@x.example"


class TestAddressesDoNotEatBusinessContent:
    @pytest.mark.parametrize(
        "text",
        [
            "Our ARR is 2 million dr.",
            "It's roughly 250 users per drive.",
            "We have about 1000 people way back in the org.",
            "That's 200 seats st across the team.",
            "Renewal is 12 months dr from now.",
        ],
    )
    def test_a_lowercase_street_word_is_not_an_address(self, text):
        # The first of these removes the budget figure from what the model
        # sees, which is the failure mode section 10.2 names.
        assert redact(text)[0] == text

    @pytest.mark.parametrize(
        "text",
        [
            "office at 1600 Pennsylvania Avenue",
            "Ship to 22 Bakers St. today",
            "They're at 450 Market Street now",
        ],
    )
    def test_a_real_address_is_still_caught(self, text):
        assert "[ADDRESS_1]" in redact(text)[0]


class TestRosterNames:
    def test_first_names_are_included_because_transcripts_use_them(self):
        from cis.redact.pii import roster_names

        names = roster_names(["Dana Chen", "Marco Diaz"])
        assert "Dana Chen" in names and "Dana" in names and "Chen" in names

    def test_name_parts_that_are_ordinary_words_are_dropped(self):
        from cis.redact.pii import roster_names

        # Redacting every "will" and "mark" removes more business content
        # than PII.
        names = roster_names(["Will Mark", "Grace Chen"])
        assert "Will" not in names
        assert "Mark" not in names
        assert "Grace" not in names
        assert "Chen" in names
        assert "Will Mark" in names, "the full name is still redacted"

    def test_initials_are_dropped(self):
        from cis.redact.pii import roster_names

        assert "J." not in roster_names(["J. Rivera"])
