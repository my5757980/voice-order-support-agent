"""T046–T047 — memory layers and reference resolution.

The tests that matter are the ones about *not* remembering: partials must not enter,
interrupted speech must be cut to what was heard, and an ambiguous referent must return
nothing rather than a guess.
"""

from __future__ import annotations

import pytest

from src.core.memory import ConversationMemory, DurableMemory, EntityContext, OrderRef

HEADPHONES = OrderRef("ORD-4471", "Aurora wireless headphones", "2026-09-03")
LAMP = OrderRef("ORD-4488", "Fold desk lamp", "2026-09-07")
MUG = OrderRef("ORD-4302", "Ridge ceramic mug", "2026-08-20")


@pytest.fixture
def ctx() -> EntityContext:
    c = EntityContext()
    c.note_order(HEADPHONES)
    c.note_order(LAMP)
    return c


# -- referring expressions -------------------------------------------------


def test_it_resolves_to_the_most_recent_order(ctx: EntityContext) -> None:
    assert ctx.resolve("where is it") == "ORD-4488"


def test_the_other_one_resolves_to_the_previous_order(ctx: EntityContext) -> None:
    """The demo moment: "what about the other one?" must not need re-identification."""
    assert ctx.resolve("what about the other one") == "ORD-4471"


def test_the_other_one_is_ambiguous_with_three_candidates(ctx: EntityContext) -> None:
    """With three orders "the other one" has no single answer, so it must not invent one."""
    ctx.note_order(MUG)
    assert ctx.resolve("the other one") is None
    assert ctx.is_ambiguous("the other one") is True


def test_item_name_resolves(ctx: EntityContext) -> None:
    assert ctx.resolve("the headphones") == "ORD-4471"


def test_unmatched_phrase_returns_none_rather_than_guessing(ctx: EntityContext) -> None:
    """FR-014 — never guess a referent when the next step could change something."""
    assert ctx.resolve("the toaster") is None


def test_empty_context_resolves_to_nothing() -> None:
    assert EntityContext().resolve("it") is None


def test_re_mentioning_an_order_makes_it_most_recent(ctx: EntityContext) -> None:
    ctx.note_order(HEADPHONES)
    assert ctx.resolve("it") == "ORD-4471"
    assert len(ctx.orders) == 2, "re-mentioning must not duplicate the entry"


# -- corrections -----------------------------------------------------------


def test_correction_replaces_the_referent(ctx: EntityContext) -> None:
    """FR-046 — "no, the other order" must stick."""
    ctx.correct_order("ORD-4488", "ORD-4471")
    assert ctx.resolve("it") == "ORD-4471"


def test_superseded_value_is_recorded_not_forgotten(ctx: EntityContext) -> None:
    """A correction the system silently forgets is worse than no correction."""
    ctx.correct_order("ORD-4488", "ORD-4471")
    assert ctx.superseded["ORD-4488"] == "ORD-4471"


# -- working memory --------------------------------------------------------


def test_turns_are_recorded_in_order() -> None:
    m = ConversationMemory()
    m.append_shopper("where is my order")
    m.append_agent("which one?")
    assert [t.speaker for t in m.turns] == ["shopper", "agent"]


def test_interrupted_agent_turn_keeps_only_what_was_heard() -> None:
    """Principle III, at the memory layer."""
    m = ConversationMemory()
    m.append_agent("It is with UPS, last seen in Memphis, arriving Thursday.", turn_id="t1")
    m.truncate_agent_turn("t1", heard_prefix_len=len("It is with UPS,"))

    turn = m.turns[-1]
    assert turn.text == "It is with UPS,"
    assert turn.truncated is True
    assert "Memphis" not in turn.text


def test_truncation_to_zero_is_legal() -> None:
    """Interrupted before a single word was heard — memory keeps nothing."""
    m = ConversationMemory()
    m.append_agent("Nothing was heard at all.", turn_id="t1")
    m.truncate_agent_turn("t1", 0)
    assert m.turns[-1].text == ""


def test_working_memory_is_bounded() -> None:
    """NFR-011 — a thirty-minute call must not send a thirty-minute prompt."""
    m = ConversationMemory(working_turns=4)
    for i in range(20):
        m.append_shopper(f"turn {i}")
    assert len(m.turns) == 4


# -- compaction ------------------------------------------------------------


def test_compaction_preserves_order_references_verbatim() -> None:
    """MC-005 — paraphrasing a reference is how an agent confirms against the wrong order."""
    m = ConversationMemory(working_turns=2)
    m.append_shopper("cancel ORD-4488 please")
    m.append_agent("done, reference R-AB1234")
    for i in range(4):
        m.append_shopper(f"filler {i}")

    assert "ORD-4488" in m.summary
    assert "R-AB1234" in m.summary


def test_summary_appears_in_the_prompt_view() -> None:
    m = ConversationMemory(working_turns=2)
    for i in range(6):
        m.append_shopper(f"turn ORD-44{i}0")
    messages = m.as_messages()
    assert messages[0]["role"] == "user"
    assert "earlier in this call" in str(messages[0]["content"])


def test_prompt_view_maps_speakers_to_roles() -> None:
    m = ConversationMemory()
    m.append_shopper("hello")
    m.append_agent("hi")
    assert [msg["role"] for msg in m.as_messages()] == ["user", "assistant"]


# -- durable memory --------------------------------------------------------


def test_durable_memory_keeps_only_three_session_summaries() -> None:
    d = DurableMemory(customer_id="cus_1")
    for i in range(6):
        d.record_session(f"session {i}")
    assert len(d.recent_session_summaries) == 3
    assert d.recent_session_summaries[-1] == "session 5"


def test_durable_memory_can_be_deleted_on_request() -> None:
    """MC-011."""
    d = DurableMemory(customer_id="cus_1", display_name="Alex", open_case_refs=["R-1"])
    d.record_session("something")
    d.forget()
    assert d.open_case_refs == []
    assert d.recent_session_summaries == []
    assert d.display_name == ""


def test_durable_memory_holds_no_transcript_field() -> None:
    """MC-009 — raw transcripts must not be storable here at all, not merely unused."""
    assert not hasattr(DurableMemory(customer_id="c"), "transcript")
    fields = set(DurableMemory(customer_id="c").__dict__)
    assert fields == {
        "customer_id", "display_name", "open_case_refs", "recent_session_summaries"
    }
