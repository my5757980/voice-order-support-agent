/**
 * DOM controller.
 *
 * Holds no conversation state of its own — it renders what the backend says. The one
 * piece of judgement it exercises is visual: a partial transcript must never look
 * settled, because partials never reach memory and the interface should not imply
 * otherwise.
 */

import { SPAN_BUDGET_MS, SPAN_LABELS, SPAN_ORDER, type AgentState } from "./protocol";

const STATE_LABEL: Record<AgentState, string> = {
  idle: "Idle",
  listening: "Listening",
  committed: "Committed",
  thinking: "Thinking",
  speaking: "Speaking",
  interrupted: "Interrupted",
  closing: "Closing",
};

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`missing element #${id}`);
  return node as T;
}

export class UI {
  private readonly transcript = el<HTMLDivElement>("transcript");
  private readonly emptyState = el<HTMLDivElement>("empty-state");
  private readonly statePill = el<HTMLDivElement>("state-pill");
  private readonly stateLabel = el<HTMLSpanElement>("state-label");
  private readonly micBtn = el<HTMLButtonElement>("mic-btn");
  private readonly micLabel = el<HTMLSpanElement>("mic-label");
  private readonly orb = el<HTMLSpanElement>("orb");
  private readonly waterfall = el<HTMLDivElement>("waterfall");
  private readonly refs = el<HTMLDivElement>("refs");
  private readonly toast = el<HTMLDivElement>("toast");
  private readonly connLabel = el<HTMLSpanElement>("conn-label");
  private readonly turnCount = el<HTMLSpanElement>("turn-count");

  private partialNode: HTMLDivElement | null = null;
  private agentNode: HTMLDivElement | null = null;
  private turns = 0;
  private toastTimer: number | null = null;
  private hasRefs = false;

  // -- conversation ------------------------------------------------------

  /** Advisory text. Replaced in place as the shopper keeps talking. */
  showPartial(text: string): void {
    this.hideEmpty();
    if (!this.partialNode) {
      this.partialNode = this.makeTurn("shopper", "You", true);
      this.transcript.appendChild(this.partialNode);
    }
    const bubble = this.partialNode.querySelector<HTMLDivElement>(".bubble");
    if (bubble) bubble.textContent = text;
    this.scroll();
  }

  /** A committed turn. This is the first point anything reaches memory. */
  commitTurn(text: string, speaker: "shopper" | "agent"): void {
    this.hideEmpty();
    if (speaker === "shopper" && this.partialNode) {
      // Promote the partial in place rather than removing and re-adding: the bubble
      // should settle, not blink.
      this.partialNode.classList.remove("partial");
      const bubble = this.partialNode.querySelector<HTMLDivElement>(".bubble");
      if (bubble) bubble.textContent = text;
      this.partialNode = null;
    } else {
      const node = this.makeTurn(speaker, speaker === "shopper" ? "You" : "Agent", false);
      const bubble = node.querySelector<HTMLDivElement>(".bubble");
      if (bubble) bubble.textContent = text;
      this.transcript.appendChild(node);
      if (speaker === "agent") this.agentNode = node;
    }
    this.turns += 1;
    this.turnCount.textContent = `${this.turns} turn${this.turns === 1 ? "" : "s"}`;
    const metric = document.getElementById("m-turns");
    if (metric) metric.textContent = String(this.turns);
    this.scroll();
  }

  /**
   * Mark the agent's turn as cut short.
   *
   * The visual matters: what remains on screen is exactly what the shopper heard, which
   * is also exactly what went into memory. Showing the full generated text here would
   * misrepresent the system's own behaviour.
   */
  markInterrupted(): void {
    const flash = document.createElement("div");
    flash.className = "barge-flash";
    flash.textContent = "interrupted";
    this.transcript.appendChild(flash);

    if (this.agentNode) {
      this.agentNode.classList.add("truncated");
      const note = document.createElement("span");
      note.className = "cut-note";
      note.textContent = "memory keeps only what you heard";
      this.agentNode.appendChild(note);
      this.agentNode = null;
    }
    this.scroll();
  }

  private makeTurn(speaker: "shopper" | "agent", label: string, partial: boolean): HTMLDivElement {
    const node = document.createElement("div");
    node.className = `turn ${speaker}${partial ? " partial" : ""}`;

    const meta = document.createElement("div");
    meta.className = "turn-meta";
    meta.textContent = partial ? `${label} · live` : label;

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    node.append(meta, bubble);
    return node;
  }

  private hideEmpty(): void {
    this.emptyState.style.display = "none";
  }

  private scroll(): void {
    this.transcript.scrollTop = this.transcript.scrollHeight;
  }

  // -- state -------------------------------------------------------------

  setState(state: AgentState): void {
    this.statePill.dataset["state"] = state;
    this.stateLabel.textContent = STATE_LABEL[state] ?? state;
  }

  setConnected(connected: boolean): void {
    this.connLabel.textContent = connected ? "Live" : "Offline";
    this.connLabel.style.color = connected ? "var(--live)" : "var(--text-faint)";
  }

  setMicActive(active: boolean): void {
    this.micBtn.setAttribute("aria-pressed", String(active));
    this.micLabel.textContent = active ? "Stop" : "Start";
  }

  setLevel(level: number): void {
    this.orb.style.setProperty("--level", level.toFixed(3));
  }

  // -- signal panel ------------------------------------------------------

  renderTimings(spans: Record<string, number>): void {
    const present = SPAN_ORDER.filter((s) => spans[s] !== undefined);
    if (present.length === 0) return;

    const scale = Math.max(...present.map((s) => spans[s] ?? 0), 100);
    this.waterfall.replaceChildren();

    for (const span of present) {
      const ms = spans[span] ?? 0;
      const budget = SPAN_BUDGET_MS[span];
      const over = budget !== undefined && ms > budget;

      const row = document.createElement("div");
      row.className = "span-row";

      const head = document.createElement("div");
      head.className = "span-head";
      const name = document.createElement("span");
      name.className = "span-name";
      name.textContent = SPAN_LABELS[span] ?? span;
      const value = document.createElement("span");
      value.className = "span-ms";
      value.textContent = `${Math.round(ms)} ms`;
      if (over) value.style.color = "var(--alert)";
      head.append(name, value);

      const track = document.createElement("div");
      track.className = "span-track";
      const fill = document.createElement("div");
      fill.className = `span-fill${over ? " over" : ""}`;
      fill.style.width = `${Math.min(100, (ms / scale) * 100)}%`;
      track.appendChild(fill);

      // A budget marker turns the bar from decoration into a verdict.
      if (budget !== undefined && budget < scale) {
        const marker = document.createElement("div");
        marker.className = "span-budget";
        marker.style.left = `${(budget / scale) * 100}%`;
        marker.title = `budget ${budget} ms`;
        track.appendChild(marker);
      }

      row.append(head, track);
      this.waterfall.appendChild(row);
    }

    this.setMetric("m-e2e", spans["e2e"], 1000);
    this.setMetric("m-barge", spans["playback.stop"], 100);
  }

  private setMetric(id: string, ms: number | undefined, budget: number): void {
    const node = document.getElementById(id);
    if (!node || ms === undefined) return;
    node.textContent = `${Math.round(ms)}`;
    const card = node.parentElement;
    card?.classList.toggle("over", ms > budget);
    card?.classList.toggle("good", ms <= budget);
  }

  addReference(kind: string, label: string, value: string): void {
    if (!this.hasRefs) {
      this.refs.replaceChildren();
      this.hasRefs = true;
    }
    const card = document.createElement("div");
    card.className = "ref-card";
    card.dataset["kind"] = kind;

    const l = document.createElement("div");
    l.className = "ref-label";
    l.textContent = label;
    const v = document.createElement("div");
    v.className = "ref-value";
    v.textContent = value;

    card.append(l, v);
    this.refs.prepend(card);
  }

  // -- feedback ----------------------------------------------------------

  notify(message: string, isError = false): void {
    this.toast.textContent = message;
    this.toast.classList.toggle("error", isError);
    this.toast.classList.add("show");
    if (this.toastTimer !== null) clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => this.toast.classList.remove("show"), 3200);
  }
}
