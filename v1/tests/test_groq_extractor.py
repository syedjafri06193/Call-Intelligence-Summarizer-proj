import json
from types import SimpleNamespace

from cis.extract.grounded import Field
from cis.extract.groq_extractor import GroqExtractor

FIELDS = [Field.BUDGET, Field.CHAMPION]


def _response(arguments: str):
    call = SimpleNamespace(function=SimpleNamespace(arguments=arguments))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))]
    )


class FakeGroq:
    def __init__(self, *argument_strings):
        self.calls = []
        self._queue = list(argument_strings)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _response(self._queue.pop(0))


def test_returns_protocol_shape():
    args = json.dumps({"budget": {"value": "$80k",
                                  "quotes": ["it's more like eighty thousand"],
                                  "support": "explicit"}})
    out = GroqExtractor(client=FakeGroq(args)).extract("...", FIELDS)
    assert out == {"budget": {"value": "$80k",
                              "quotes": ["it's more like eighty thousand"],
                              "confidence": 0.9}}

def test_implied_support_stays_below_task_threshold():
    args = json.dumps({"commitment": {"value": "maybe send it",
                                      "quotes": ["I might send it"],
                                      "support": "implied"}})
    out = GroqExtractor(client=FakeGroq(args)).extract("...", [Field.COMMITMENT])
    assert out["commitment"]["confidence"] < 0.7


def test_participant_id_is_passed_through():
    args = json.dumps({"commitment": {"value": "send questionnaire",
                                      "quotes": ["I'll send it Friday"],
                                      "support": "explicit",
                                      "participant_id": "p_rep"}})
    out = GroqExtractor(client=FakeGroq(args)).extract("...", [Field.COMMITMENT])
    assert out["commitment"]["subject_participant_id"] == "p_rep"


def test_malformed_json_is_retried_once():
    good = json.dumps({"budget": {"value": "$5k", "quotes": ["five thousand"]}})
    fake = FakeGroq("{not valid json", good)
    out = GroqExtractor(client=fake).extract("...", FIELDS)
    assert "budget" in out and len(fake.calls) == 2


def test_gives_up_with_empty_result_not_a_guess():
    fake = FakeGroq("{bad", "{still bad")
    assert GroqExtractor(client=fake).extract("...", FIELDS) == {}


def test_drops_unknown_field_and_blank_quotes():
    args = json.dumps({
        "weather": {"value": "sunny", "quotes": ["nice day"]},
        "budget": {"value": "$5k", "quotes": ["  "]},
    })
    assert GroqExtractor(client=FakeGroq(args)).extract("...", FIELDS) == {}


def test_tool_is_forced_and_deterministic():
    fake = FakeGroq(json.dumps({}))
    GroqExtractor(client=fake).extract("hello", FIELDS)
    call = fake.calls[0]
    assert call["tool_choice"]["function"]["name"] == "record_claims"
    assert call["temperature"] == 0
    assert call["messages"][1]["content"].count("<transcript>") == 1


def test_empty_chunk_makes_no_call():
    fake = FakeGroq()
    assert GroqExtractor(client=fake).extract("  ", FIELDS) == {}
    assert fake.calls == []