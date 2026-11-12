"""Hosting / provider compatibility: hosted DATABASE_URL forms and Gemini thought signatures."""

from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessage

from app.ai.providers import OpenAICompatibleProvider
from app.core.config import Settings


@pytest.mark.parametrize(
    "raw",
    ["postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"],
)
def test_database_url_normalised_to_psycopg(raw):
    assert Settings(database_url=raw).database_url == "postgresql+psycopg://u:p@h:5432/db"


def test_tool_round_echoes_gemini_thought_signature():
    msg = ChatCompletionMessage.model_validate(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_generation_summary", "arguments": "{}"},
                    "extra_content": {"google": {"thought_signature": "sig-abc"}},
                }
            ],
        }
    )
    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    out = provider.tool_result_messages(msg, [{"id": "call_1", "content": "{}"}])
    tc = out[0]["tool_calls"][0]
    assert tc["extra_content"] == {"google": {"thought_signature": "sig-abc"}}
    assert tc["function"] == {"name": "get_generation_summary", "arguments": "{}"}
    assert out[1] == {"role": "tool", "tool_call_id": "call_1", "content": "{}"}


def test_tool_round_plain_openai_message_unchanged():
    tc = SimpleNamespace(id="c", function=SimpleNamespace(name="n", arguments=None))
    d = OpenAICompatibleProvider._tool_call_dict(tc)
    assert d == {"id": "c", "type": "function", "function": {"name": "n", "arguments": "{}"}}
