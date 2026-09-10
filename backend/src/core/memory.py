"""Conversation memory: working, summary, and durable.

Deliberately forgetful. Each layer has a distinct purpose and its own retention rule,
and the boundaries between them are the point — a design where "memory" is one growing
list is how a thirty-minute call ends up sending a thirty-minute prompt.

Two rules run through all of it:

  **Only committed turns enter.** A partial transcript is advisory; it drives the live
  pane and the barge-in decision and nothing else (principle II).

  **An agent turn is truncated to what was HEARD.** Not to what was generated. Memory
  that records words the shopper never heard poisons every subsequent turn (principle III).
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

Speaker = Literal["shopper", "agent"]


@dataclass
class Turn:
    speaker: Speaker
    text: str
    turn_id: str = ""
    turn_order: int = 0
    truncated: bool = False
    """Set when the shopper interrupted; `text` is then the heard prefix only."""


@dataclass(frozen=True, slots=True)
class OrderRef:
    order_id: str
    item_summary: str
    placed_at: str


@dataclass
class EntityContext:
    """What "it", "that one" and "the other one" refer to.

    Orders are insertion-ordered, which is what makes "the other one" answerable: it
    means the one discussed before the most recent, not an arbitrary other.
    """

    orders: OrderedDict[str, OrderRef] = field(default_factory=OrderedDict)
    last_order_id: str | None = None
    last_line_item_id: str | None = None
    pending_confirmation: dict[str, Any] | None = None
    superseded: dict[str, str] = field(default_factory=dict)

    def note_order(self, ref: OrderRef) -> None:
        # Re-mentioning an order moves it to most-recent rather than duplicating it.
        self.orders.pop(ref.order_id, None)
        self.orders[ref.order_id] = ref
        self.last_order_id = ref.order_id

    def correct_order(self, wrong_id: str, right_id: str) -> None:
        """The shopper said "no, the other order" (FR-046, MC-004).

        The superseded value is recorded so it is never silently reused — a correction
        the system forgets is worse than no correction at all.
        """
        self.superseded[wrong_id] = right_id
        self.last_order_id = right_id

    def resolve(self, phrase: str) -> str | None:
        """Resolve a referring expression to an order id, or None if ambiguous.

        Returning None is a real answer: the orchestrator must ask rather than guess
        whenever the pending action changes state (FR-014).
        """
        text = phrase.lower().strip()
        ids = list(self.orders)

        if not ids:
            return None

        if any(w in text for w in ("other", "another", "first one", "previous")):
            # "the other one" needs exactly two candidates to be unambiguous.
            if len(ids) == 2:
                return ids[0] if self.last_order_id == ids[1] else ids[1]
            return None

        if any(w in text for w in ("it", "that", "this", "same", "the one")):
            return self.last_order_id

        # Match on item name — "the blue one", "the headphones".
        matches = [
            oid for oid, ref in self.orders.items()
            if any(word in ref.item_summary.lower() for word in text.split() if len(word) > 3)
        ]
        return matches[0] if len(matches) == 1 else None

    def is_ambiguous(self, phrase: str) -> bool:
        return self.resolve(phrase) is None and len(self.orders) > 1


class ConversationMemory:
    """Implements the ConversationMemory port."""

    def __init__(self, *, working_turns: int = 12) -> None:
        self._working: deque[Turn] = deque(maxlen=working_turns)
        self._summary: str = ""
        self._evicted: list[Turn] = []
        self.entities = EntityContext()

    # -- writes ------------------------------------------------------------

    def append_shopper(self, text: str, *, turn_id: str = "", turn_order: int = 0) -> None:
        self._push(Turn(speaker="shopper", text=text, turn_id=turn_id, turn_order=turn_order))

    def append_agent(self, text: str, *, turn_id: str = "") -> None:
        self._push(Turn(speaker="agent", text=text, turn_id=turn_id))

    def truncate_agent_turn(self, turn_id: str, heard_prefix_len: int) -> None:
        """Cut the agent's turn to what the shopper actually heard."""
        for turn in reversed(self._working):
            if turn.speaker == "agent" and (not turn_id or turn.turn_id == turn_id):
                turn.text = turn.text[:heard_prefix_len].rstrip()
                turn.truncated = True
                return

    def _push(self, turn: Turn) -> None:
        # A deque with maxlen drops silently; capture the eviction so compaction can
        # preserve what must survive (MC-005).
        if len(self._working) == self._working.maxlen:
            self._evicted.append(self._working[0])
            self._compact()
        self._working.append(turn)

    # -- compaction --------------------------------------------------------

    def _compact(self) -> None:
        """Fold evicted turns into the running summary.

        Order references, actions taken and reference numbers are preserved verbatim —
        those are the facts a later turn will need to act on, and paraphrasing them is
        how an agent ends up confirming a return against the wrong order.
        """
        if not self._evicted:
            return
        facts: list[str] = []
        for turn in self._evicted:
            for token in turn.text.split():
                cleaned = token.strip(".,!?;:")
                if _is_reference(cleaned) and cleaned not in facts:
                    facts.append(cleaned)
        lines = [f"{t.speaker}: {t.text}" for t in self._evicted]
        addition = " | ".join(lines)
        if facts:
            addition += f" [references: {', '.join(facts)}]"
        self._summary = (self._summary + " " + addition).strip()
        self._evicted.clear()

    # -- reads -------------------------------------------------------------

    @property
    def turns(self) -> list[Turn]:
        return list(self._working)

    @property
    def summary(self) -> str:
        return self._summary

    def as_messages(self) -> list[dict[str, object]]:
        """Prompt-shaped view: a summary block if one exists, then recent turns verbatim."""
        messages: list[dict[str, object]] = []
        if self._summary:
            messages.append(
                {"role": "user", "content": f"[earlier in this call] {self._summary}"}
            )
        for turn in self._working:
            messages.append(
                {
                    "role": "user" if turn.speaker == "shopper" else "assistant",
                    "content": turn.text,
                }
            )
        return messages


def _is_reference(token: str) -> bool:
    """Order ids, return references, tracking numbers — anything a later turn may act on."""
    if len(token) < 4:
        return False
    upper = token.upper()
    if upper.startswith(("ORD-", "R-", "T-")):
        return True
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


@dataclass
class DurableMemory:
    """Across sessions. Deliberately minimal — this is the complete list (MC-008).

    Raw transcripts never appear here, and never enter a later session's active context
    (MC-009).
    """

    customer_id: str
    display_name: str = ""
    open_case_refs: list[str] = field(default_factory=list)
    recent_session_summaries: list[str] = field(default_factory=list)

    MAX_SUMMARIES = 3

    def record_session(self, summary: str) -> None:
        self.recent_session_summaries.append(summary)
        del self.recent_session_summaries[: -self.MAX_SUMMARIES]

    def forget(self) -> None:
        """Deletion on shopper request (MC-011)."""
        self.open_case_refs.clear()
        self.recent_session_summaries.clear()
        self.display_name = ""
