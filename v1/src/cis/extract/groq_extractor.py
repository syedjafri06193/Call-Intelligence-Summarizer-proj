from __future__ import annotations
from .llm_extractor import SYSTEM_PROMPT, build_tool, build_user_message, clean_claims
import json
import logging
import os
from typing import Any, Sequence
from .grounded import Field
logger = logging.getLogger(__name__)

# Check console.groq.com/docs/models for what your account can use.
DEFAULT_GROQ_MODEL = os.getenv("CIS_GROQ_MODEL", "llama-3.3-70b-versatile")


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        },
    }


class GroqExtractor:
    """Implements ExtractionModel using Groq's chat completions API."""

    def __init__(self, client: Any = None, model: str = DEFAULT_GROQ_MODEL,
                 max_tokens: int = 2000, attempts: int = 2):
        if client is None:
            from groq import Groq  # lazy: demo and tests need no SDK
            client = Groq(max_retries=3)  # reads GROQ_API_KEY itself
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.attempts = attempts
        self.seen: list[str] = []  # what the model received (the demo prints it)

    def extract(self, chunk_text: str, fields: Sequence[Field]) -> dict:
        if not fields or not chunk_text.strip():
            return {}
        self.seen.append(chunk_text)

        tool = _to_openai_tool(build_tool(fields))
        allowed = {f.value for f in fields}

        for attempt in range(1, self.attempts + 1):
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",
                     "content": build_user_message(chunk_text, fields)},
                ],
                tools=[tool],
                tool_choice={"type": "function",
                             "function": {"name": tool["function"]["name"]}},
            )
            args = self._tool_arguments(resp)
            if args is not None:
                return self._clean(args, allowed)
            logger.warning("groq returned unparseable tool arguments "
                           "(attempt %d/%d)", attempt, self.attempts)
        return {}

    @staticmethod
    def _tool_arguments(resp: Any) -> dict | None:
        calls = getattr(resp.choices[0].message, "tool_calls", None) or []
        for call in calls:
            try:
                parsed = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _clean(args: dict, allowed: set[str]) -> dict:
        out: dict = {}
        for name, payload in args.items():
            if name not in allowed or not isinstance(payload, dict):
                continue
            quotes = [q for q in payload.get("quotes", [])
                      if isinstance(q, str) and q.strip()]
            value = payload.get("value")
            if not quotes or not value:
                continue
            # No "confidence": a number a model reports about itself is not a
            # measurement. The pipeline's default of 0.5 applies.
            out[name] = {"value": str(value), "quotes": quotes}
        return out

    @staticmethod
    def _clean(args: dict, allowed: set[str]) -> dict:
        return clean_claims(args, allowed)