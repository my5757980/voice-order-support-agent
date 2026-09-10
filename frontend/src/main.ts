/**
 * Wiring: audio engine ↔ transport ↔ interface.
 *
 * The one behaviour worth reading closely is `audio.flush`. When the backend decides the
 * shopper has interrupted, it fires three things concurrently — the TTS `clear_buffer`,
 * this flush, and cancellation of the turn. This handler must not await anything before
 * flushing: the server-side stop halts *production* of audio, but what the shopper is
 * hearing right now is already sitting in the local ring buffer, and only this call
 * silences it.
 */

import { AudioEngine } from "./audio";
import type { ServerMessage } from "./protocol";
import { Transport } from "./transport";
import { UI } from "./ui";

const ui = new UI();
let currentTurnId = "";

const transport = new Transport({
  onOpen: () => {
    ui.setConnected(true);
    ui.notify("Connected");
  },
  onClose: (reason) => {
    ui.setConnected(false);
    ui.setState("idle");
    ui.setMicActive(false);
    void audio.stop();
    ui.notify(reason, true);
  },
  onAudio: (pcm) => audio.enqueue(pcm, currentTurnId),
  onMessage: handleMessage,
});

const audio = new AudioEngine({
  onFrame: (frame) => transport.sendFrame(frame),
  onLevel: (level) => ui.setLevel(level),
  onPlaybackProgress: (turnId, framesPlayed) => {
    transport.send({ type: "playback.progress", turn_id: turnId, frames_played: framesPlayed });
  },
  onFlushed: (turnId, framesPlayed) => {
    // Report what was actually played at the moment of the cut. Combined with TTS
    // character alignment server-side, this is what makes `heard_prefix_len` exact
    // rather than an estimate from elapsed time.
    transport.send({ type: "playback.progress", turn_id: turnId, frames_played: framesPlayed });
  },
});

function handleMessage(msg: ServerMessage): void {
  switch (msg.type) {
    case "session.ready":
      ui.notify("Session ready");
      break;

    case "transcript.partial":
      ui.showPartial(msg.text);
      break;

    case "transcript.committed":
      ui.commitTurn(msg.text, msg.speaker);
      break;

    case "agent.speaking":
      currentTurnId = msg.turn_id;
      ui.setState("speaking");
      break;

    case "agent.done":
      if (msg.status === "interrupted") ui.markInterrupted();
      ui.setState("idle");
      break;

    case "audio.flush":
      // No await, no batching, no confirmation round trip. Straight to the ring buffer.
      audio.flush();
      ui.markInterrupted();
      ui.setState("interrupted");
      break;

    case "display.reference":
      ui.addReference(msg.kind, msg.label, msg.value);
      break;

    case "state":
      ui.setState(msg.state);
      break;

    case "timing":
      ui.renderTimings(msg.spans);
      break;

    case "status":
      if (msg.message) ui.notify(msg.message);
      break;

    case "error":
      // Shopper-safe text only — the backend never sends a stack trace or a vendor error.
      ui.notify(msg.message, true);
      break;
  }
}

// -- controls ---------------------------------------------------------------

const micBtn = document.getElementById("mic-btn") as HTMLButtonElement;
micBtn.addEventListener("click", async () => {
  if (audio.running) {
    transport.send({ type: "mic.state", active: false });
    await audio.stop();
    transport.close();
    ui.setMicActive(false);
    ui.setConnected(false);
    ui.setState("idle");
    return;
  }

  try {
    // Output first, and before the sockets: the backend greets as soon as both are
    // open, so the playback node has to exist before the greeting can arrive.
    await audio.startPlayback();
    await transport.connect();

    // Then the microphone. A refusal here is not a failed session — the shopper types
    // and still hears every reply, which is the whole point of the text fallback.
    const mic = await audio.startCapture();
    transport.send({ type: "mic.state", active: mic === "capturing" });
    ui.setMicActive(true);
    if (mic === "capturing") {
      ui.setState("listening");
    } else {
      ui.setState("idle");
      ui.notify(
        mic === "blocked"
          ? "Microphone blocked — type below, you'll still hear the replies"
          : "No microphone found — type below, you'll still hear the replies",
        true,
      );
    }
  } catch (err) {
    // Backend-down and no-audio-output are different problems and deserve different
    // words — "something went wrong" would leave the shopper with nothing to act on.
    ui.notify(err instanceof Error ? err.message : "Could not start the session", true);
    await audio.stop();
    transport.close();
    ui.setConnected(false);
  }
});

// Text fallback: every voice capability must be reachable without a microphone.
const textForm = document.getElementById("text-form") as HTMLFormElement;
const textInput = document.getElementById("text-input") as HTMLInputElement;
textForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = textInput.value.trim();
  if (!text) return;
  if (!transport.connected) {
    ui.notify("Start the session first", true);
    return;
  }
  // Rendered when the server echoes transcript.committed, exactly as a spoken turn is.
  // Drawing it here as well showed every typed turn twice, and made the text path the
  // one place where the pane displayed something the orchestrator had not accepted.
  transport.send({ type: "text.input", text });
  textInput.value = "";
});

// -- theme ------------------------------------------------------------------

const themeBtn = document.getElementById("theme-btn") as HTMLButtonElement;
const themeIcon = document.getElementById("theme-icon") as HTMLSpanElement;

function applyTheme(theme: string | null): void {
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
  themeIcon.textContent = theme === "light" ? "☀" : theme === "dark" ? "☾" : "◐";
}

try {
  applyTheme(localStorage.getItem("theme"));
} catch {
  // Private browsing or blocked site data — the system theme is a fine fallback.
}

themeBtn.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  const next = current === "dark" ? "light" : current === "light" ? null : "dark";
  applyTheme(next);
  try {
    if (next) localStorage.setItem("theme", next);
    else localStorage.removeItem("theme");
  } catch {
    /* storage unavailable — the toggle still works for this page view */
  }
});

// Leaving mid-conversation should end the session cleanly rather than leaving a
// server-side task group orphaned.
window.addEventListener("beforeunload", () => {
  if (transport.connected) transport.close();
});
