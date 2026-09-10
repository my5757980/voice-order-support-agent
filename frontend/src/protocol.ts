/**
 * Browser ↔ backend control-socket protocol.
 *
 * Mirrors specs/001-order-support-agent/contracts/websocket-protocol.md. Audio travels
 * on a separate binary socket so control messages never queue behind audio frames —
 * `audio.flush` in particular must never wait in line, because it is the message that
 * stops the agent talking.
 */

export type AgentState =
  | "idle"
  | "listening"
  | "committed"
  | "thinking"
  | "speaking"
  | "interrupted"
  | "closing";

/** Backend → browser. */
export type ServerMessage =
  | { type: "session.ready"; session_id: string }
  | { type: "transcript.partial"; text: string; turn_order: number }
  | {
      type: "transcript.committed";
      text: string;
      text_formatted?: string;
      turn_order: number;
      speaker: "shopper" | "agent";
    }
  | { type: "agent.speaking"; turn_id: string }
  | { type: "agent.done"; turn_id: string; status: "complete" | "interrupted" }
  | { type: "audio.flush"; turn_id: string }
  | {
      type: "display.reference";
      kind: "tracking" | "order" | "link" | "return";
      value: string;
      label: string;
    }
  | { type: "state"; state: AgentState }
  | { type: "timing"; turn_id: string; spans: Record<string, number> }
  | { type: "status"; state: string; message?: string }
  | { type: "error"; code: string; message: string };

/** Browser → backend. */
export type ClientMessage =
  | { type: "mic.state"; active: boolean }
  | { type: "text.input"; text: string }
  | { type: "session.end" }
  | { type: "playback.progress"; turn_id: string; frames_played: number };

/** The seven mandatory spans plus the barge-in measurement this feature adds. */
export const SPAN_ORDER = [
  "audio.capture",
  "stt.turn",
  "llm.ttft",
  "llm.complete",
  "tool",
  "tts.ttfb",
  "playback.start",
  "playback.stop",
] as const;

export const SPAN_LABELS: Record<string, string> = {
  "audio.capture": "Capture",
  "stt.turn": "Transcribe",
  "llm.ttft": "First token",
  "llm.complete": "Generate",
  tool: "Tools",
  "tts.ttfb": "First audio",
  "playback.start": "Playback",
  "playback.stop": "Interrupt",
};

/** p95 budgets from the constitution. Shown in the UI so a regression is visible live. */
export const SPAN_BUDGET_MS: Record<string, number> = {
  "audio.capture": 50,
  "stt.turn": 300,
  "llm.ttft": 450,
  "tts.ttfb": 250,
  "playback.stop": 100,
};
