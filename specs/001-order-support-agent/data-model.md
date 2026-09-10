# Phase 1 Data Model: Voice Order Support Agent

**Feature**: `001-order-support-agent` | **Date**: 2026-09-09
**Source**: Entities from [spec.md](./spec.md) § Key Entities; decisions from [research.md](./research.md)

Two distinct groups live here and must not be conflated:

- **Domain entities** — the simulated commerce data, persisted in SQLite, shaped like the real order
  service they will one day be replaced by.
- **Conversation entities** — per-session runtime state, in-memory, owned solely by the Orchestrator.

Only `DurableMemory` and audit rows cross from the second group into storage.

---

## Domain entities (SQLite, seeded)

### Customer

| Field | Type | Rules |
|---|---|---|
| `customer_id` | str (uuid) | PK. Established only by the authenticated store session — never by voice (FR-001) |
| `display_name` | str | Spoken in greetings |
| `created_at` | datetime | |

### Order

| Field | Type | Rules |
|---|---|---|
| `order_id` | str | PK. Human-speakable reference; last 4 chars usable as a partial identifier (FR-021) |
| `customer_id` | str | FK → Customer. **Every query filters on this, server-side** (FR-003) |
| `placed_at` | datetime | Orders older than 90 days are excluded from automatic identification (Assumption 5) |
| `status` | enum | `placed` \| `processing` \| `partially_shipped` \| `shipped` \| `delivered` \| `cancelled` |
| `total_cents` | int | Integer cents — never float. Spoken naturally per VID-007 |
| `currency` | str(3) | |

**Invariants**: `cancel_order` is legal only when no `Shipment` exists (FR-027). `status` is
re-read before any action that depends on it (MC-013) — never trusted from memory.

### LineItem

| Field | Type | Rules |
|---|---|---|
| `line_item_id` | str | PK |
| `order_id` | str | FK → Order |
| `product_id` | str | FK → Product |
| `quantity` | int | ≥ 1 |
| `unit_price_cents` | int | |
| `return_window_closes_at` | datetime \| null | **Per line item, not per order** — the spec is explicit |
| `return_state` | enum | `eligible` \| `window_closed` \| `returned` \| `not_returnable` |

**Invariant**: return eligibility is evaluated per line item. An order may be half returnable, and
the agent must be able to say so.

### Shipment

| Field | Type | Rules |
|---|---|---|
| `shipment_id` | str | PK |
| `order_id` | str | FK → Order. **One order may have many** — split shipments are a required edge case |
| `carrier` | str | |
| `tracking_reference` | str | Offered on screen, never spelled aloud (FR-020, VID-006) |
| `latest_scan_location` | str \| null | |
| `latest_scan_at` | datetime \| null | |
| `expected_delivery` | date \| null | |

**Invariant**: if no `Shipment` row exists, the agent must not state a carrier or tracking number
(FR-019). Absence of a shipment is a first-class answer, not a missing value to paper over.

### Product

| Field | Type | Rules |
|---|---|---|
| `product_id` | str | PK |
| `name` | str | Used for disambiguation by item name (FR-016) |
| `attributes` | json | Answers product questions — **only** from here (FR-037) |
| `care_text` | str \| null | |

**Security note**: `attributes` and `care_text` are merchant-supplied free text and are treated as
**data, never instructions** (FR-049). They are rendered into the prompt inside a delimited block
that the system prompt declares non-authoritative.

### Policy

| Field | Type | Rules |
|---|---|---|
| `topic` | str | PK. e.g. `return_window`, `refund_timing`, `shipping` |
| `body` | str | The answer text |
| `params` | json | Values applied against a specific order (e.g. `window_days: 30`) |

### ReturnRequest

| Field | Type | Rules |
|---|---|---|
| `return_id` | str | PK. Stated to the shopper (FR-026) |
| `order_id` | str | FK → Order |
| `line_item_ids` | json[str] | Non-empty |
| `reason` | str | |
| `status` | enum | `created` \| `label_issued` \| `in_transit` \| `refunded` \| `failed` |
| `refund_estimate_days` | int | |
| `idempotency_key` | str | **UNIQUE.** Derived from `turn_id` (FR-029, TC-004) |
| `created_by_turn_id` | str | Audit trail |

**Invariant**: the unique constraint on `idempotency_key` is what makes duplicate prevention a
*database* guarantee rather than an application check. A repeated request within a session returns
the existing row.

### EscalationTicket

| Field | Type | Rules |
|---|---|---|
| `ticket_id` | str | PK. Stated to the shopper with expected response time (FR-035) |
| `session_id` | str | |
| `summary` | str | Conversation summary (FR-034) |
| `order_refs` | json[str] | Orders discussed |
| `actions_taken` | json | What the agent already did |
| `transcript_ref` | str | Pointer, not the transcript itself |
| `expected_response_hours` | int | |

### ToolAuditEntry

| Field | Type | Rules |
|---|---|---|
| `audit_id` | str | PK |
| `session_id`, `turn_id` | str | |
| `tool_name` | str | |
| `arguments_redacted` | json | **Redacted before write** (FR-064) |
| `outcome` | enum | `success` \| `validation_failed` \| `authz_denied` \| `error` \| `deduplicated` |
| `duration_ms` | int | |
| `actor` | enum | `agent` \| `human` — agent actions are distinguishable (FR-065) |

---

## Conversation entities (in-memory, per session)

### ConversationSession

```python
session_id: str
customer_id: str
started_at: datetime
state: SessionState                  # see state machine below
turns: list[Turn]                    # committed only
entities: EntityContext              # for referring expressions
actions_taken: list[ActionRecord]
outcome: Literal["resolved","escalated","abandoned"] | None
```

### Turn

```python
turn_id: str                         # our uuid
turn_order: int                      # from AssemblyAI — canonical ordering (FR/principle II)
speaker: Literal["shopper","agent"]
text: str                            # unformatted on the critical path
text_formatted: str | None           # display and logs only
committed: bool                      # True only when end_of_turn == True
heard_prefix_len: int | None         # agent turns: chars actually played before interruption
timings: TurnTimings
```

**`heard_prefix_len` is the field that makes principle III real.** On interruption the agent turn is
truncated to this length before entering memory — computed from TTS character-alignment timestamps
cross-referenced with frames the playback worklet actually released. Not from what the LLM
generated, and not estimated from elapsed time.

### EntityContext

Resolves referring expressions — "it", "that one", "the other one", "the blue one" (FR-042).

```python
orders_discussed: OrderedDict[str, OrderRef]    # insertion-ordered → "the other one"
last_order_ref: str | None
last_line_item_ref: str | None
pending_confirmation: PendingAction | None      # gates the FR-010 affirmative override
superseded: dict[str, str]                      # corrections (FR-046, MC-004)
```

**Rule**: on a state-changing action, if the referent is ambiguous the orchestrator MUST ask rather
than resolve (FR-014). Ambiguity is resolved only for read-only intents.

### PendingAction

```python
action: Literal["create_return","cancel_order","update_shipping_address"]
target: dict                         # order_id, line_item_ids, address...
read_back_text: str                  # exactly what was spoken (VID-016)
asked_at_turn: int
```

Non-null `pending_confirmation` is what flips a bare "yes" from backchannel to answer — the FR-009 /
FR-010 collision, resolved by one field consulted first in the barge-in decision.

### Memory layers

```python
working:  deque[Turn]                # last 12 committed turns, verbatim (MC-001)
summary:  str                        # compacted older turns; preserves order refs,
                                     # actions, reference numbers VERBATIM (MC-005)
durable:  DurableMemory              # persisted, deliberately minimal
```

```python
class DurableMemory:                 # MC-008 — this is the complete list
    customer_id: str
    display_name: str
    open_case_refs: list[str]
    recent_session_summaries: list[str]   # max 3
```

**Never in durable memory**: raw transcripts (MC-009), payment details, passwords, identifiers
(MC-014) — even if the shopper volunteers them.

---

## State machines

### Session / turn state

```text
        ┌──────────────────────── interrupt ─────────────────────┐
        │                                                        │
        ▼                                                        │
IDLE ──speech──▶ LISTENING ──end_of_turn──▶ COMMITTED ──▶ THINKING ──▶ SPEAKING
 ▲                   │                                        │            │
 │                   │ silence 6s → reprompt                  │            │
 │                   │ silence 18s → reprompt                 │            │
 │                   │ silence 30s → CLOSING                  │            │
 │                                                            ▼            ▼
 └──────────────── truncate memory ◀────────────────────── INTERRUPTED ◀───┘
                                                                 │
                                                                 └──▶ LISTENING
```

| Transition | Trigger | Side effect |
|---|---|---|
| `LISTENING → COMMITTED` | `Turn(end_of_turn=true)` | Write to working memory; update EntityContext |
| `COMMITTED → THINKING` | Orchestrator dispatch | Open `turn_scope` TaskGroup |
| `THINKING → SPEAKING` | First TTS audio frame released | Emit `playback.start` |
| `SPEAKING → INTERRUPTED` | BargeInFilter returns INTERRUPT | `clear_buffer` + client flush + `turn_scope.cancel()` |
| `INTERRUPTED → LISTENING` | Truncation complete | Agent turn truncated to `heard_prefix_len` |
| any `→ CLOSING` | 30 s silence, or shopper ends | State summarized into durable memory |

### ReturnRequest lifecycle

```text
(none) ──create_return──▶ created ──▶ label_issued ──▶ in_transit ──▶ refunded
   ▲                         │
   └───── failed ◀───────────┘      failed → agent states it did NOT succeed (FR-030)
```

A repeated `create_return` with the same `turn_id`-derived idempotency key returns the **existing**
row with outcome `deduplicated` — it does not create a second return and does not error.

---

## Validation rules (enforced, not assumed)

| Rule | Where enforced | Requirement |
|---|---|---|
| Every query scoped to `customer_id` | Repository layer, from session — never from tool arguments | FR-003, TC-003 |
| Tool arguments match schema | ToolRegistry, before dispatch; plus `strict: true` at the provider | FR-047, TC-002 |
| State-changing action requires confirmation | Orchestrator: `pending_confirmation` must be non-null and affirmed | FR-023, SC-010 |
| No duplicate side effects | DB unique constraint on `idempotency_key` | FR-029, TC-004 |
| Return only within window | Repository checks `return_window_closes_at` server-side | FR-025 |
| Cancel only before shipment | Repository checks shipment count | FR-027 |
| No payment credentials accepted | ToolRegistry rejects; orchestrator interrupts and redirects | FR-063, TC-005 |
| Merchant text is data | Delimited, declared non-authoritative in system prompt | FR-049, TC-006 |
| Context stays bounded | Compaction at 12 working turns | MC-007, NFR-011 |

Two of these deserve emphasis because they are the ones that most often decay into convention:
customer scoping is taken **from the authenticated session, never from a tool argument** — if the
model proposes a `customer_id`, it is ignored; and duplicate prevention is a **database
constraint**, so it holds even if application logic is wrong.
