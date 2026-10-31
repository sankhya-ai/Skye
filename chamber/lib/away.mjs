// Away Responder for Chamber.
//
// Makes the "Ashish was in a meeting, his agent answered" story real across
// ALL harnesses (Claude Code, Codex, Antigravity, and DeepSeek), not just Claude's idle hook.
//
// Privacy & Safety Guarantees:
// 1. Authorization: Only answers from public team memory and public channels (#channel).
//    NEVER indexes, searches or cites 1-on-1 private direct messages.
// 2. Loop Guard: Ignores messages from */away or messages tagged as away-response to prevent
//    infinite ping-pong loops between automated agents.
// 3. E2EE Integrity: No plaintext leaves the machine unless explicitly opted in with CHAMBER_AWAY_LLM=1.

import { LineClient } from "./client.mjs";
import { describe, Inbox, loadConfig } from "./inbox.mjs";
import { modelKey, DEFAULT_MODEL } from "./dream.mjs";
import { ChamberSearch } from "./search.mjs";
import { TeamMemory } from "./memory.mjs";

const STOPWORDS = new Set([
  "a", "about", "an", "and", "are", "as", "at", "be", "by", "can", "do",
  "for", "from", "how", "in", "is", "it", "me", "my", "of", "on", "or",
  "that", "the", "there", "this", "to", "we", "what", "whats", "which", "you", "your",
]);

function extractKeywords(query) {
  const words = String(query || "")
    .toLowerCase()
    .replace(/[^\w\s]/g, " ")
    .split(/\s+/)
    .map((w) => w.trim())
    .filter((w) => w.length > 2 && !STOPWORDS.has(w));
  return words.length ? words.join(" ") : String(query || "").trim();
}

export class AwayResponder {
  constructor(config = loadConfig(), { cwd = process.cwd(), statusMessage = "away in a meeting" } = {}) {
    if (!config) throw new Error("not in a chamber yet (run chamber join or chamber new)");
    this.config = config;
    this.owner = config.owner;
    this.cwd = cwd;
    this.statusMessage = statusMessage;
    this.agent = `${this.owner}/away`;
    this.search = new ChamberSearch(this.owner);
    this.memory = new TeamMemory(config.invite, this.agent);
    this.inbox = new Inbox(this.agent);
    this.client = null;
    this.running = false;
  }

  async start() {
    this.client = new LineClient(this.config.invite, this.agent, {
      kind: "away-responder",
      label: `Away responder for ${this.owner} (${this.statusMessage})`,
      priority: -1, // lower priority than interactive agents
      answers: true,
      onMessage: (msg) => this.#handleMessage(msg),
    });

    this.client.run();
    await this.client.ready();
    this.running = true;
    return this;
  }

  async stop() {
    this.running = false;
    if (this.client) {
      this.client.close();
      this.client = null;
    }
    this.search.close();
  }

  async handleMessage(message) {
    return this.#handleMessage(message);
  }

  async #handleMessage(message) {
    if (!this.running) return;

    // Loop Guard: never reply to own messages, broadcasts, other away responders, or away responses
    if (
      message.from.startsWith(`${this.owner}/`) ||
      message.from.endsWith("/away") ||
      message.to === "*" ||
      message.type === "away-response" ||
      message.awayResponse === true
    ) {
      return;
    }

    const text = message.text || "";
    if (!text.trim()) return;

    // Extract meaningful search terms (remove stopwords so FTS matches effectively)
    const keywords = extractKeywords(text);

    // 1. Search team memory (shared chamber knowledge)
    const memoryHits = await this.memory.search(keywords);

    // 2. Search ONLY public channel discussions (#channel) - NEVER 1:1 private DMs
    const rawSearchHits = await this.search.search({ query: keywords, limit: 10 });
    const searchHits = rawSearchHits
      .filter((s) => String(s.channel || "").startsWith("#"))
      .slice(0, 3);

    let synthesizedAnswer = "";
    const key = modelKey();

    // Only contact external LLM if user explicitly configured CHAMBER_AWAY_LLM=1
    if (key && process.env.CHAMBER_AWAY_LLM === "1") {
      try {
        const prompt = [
          `You are an automated away responder for ${this.owner}, who is currently ${this.statusMessage}.`,
          `A teammate's coding agent (${message.from}) asked: "${text}".`,
          `Here is relevant team memory and public channel discussions:`,
          ...memoryHits.slice(0, 3).map((h) => `- ${h.path}:${h.line}: ${h.text}`),
          ...searchHits.map((s) => `- [${s.citation}]: ${s.text}`),
          "",
          `Answer their question accurately and concisely based ONLY on the context above. If the context contains a schema, contract, or type, state it clearly and cite the file. If you do not have enough information, politely say ${this.owner} will review their question upon returning.`,
        ].join("\n");

        const res = await fetch("https://openrouter.ai/api/v1/chat/completions", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${key}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            model: DEFAULT_MODEL,
            messages: [{ role: "user", content: prompt }],
            temperature: 0.1,
            max_tokens: 400,
          }),
          signal: AbortSignal.timeout(10000),
        });

        if (res.ok) {
          const data = await res.json();
          synthesizedAnswer = data?.choices?.[0]?.message?.content?.trim() || "";
        }
      } catch {
        // Fall back to local rule-based citations
      }
    }

    if (!synthesizedAnswer) {
      if (memoryHits.length > 0) {
        synthesizedAnswer = `${this.owner} is ${this.statusMessage}. From team memory (${memoryHits[0].path}):\n${memoryHits[0].text}`;
      } else if (searchHits.length > 0) {
        const chan = searchHits[0].channel ? `${searchHits[0].channel} · ` : "";
        synthesizedAnswer = `${this.owner} is ${this.statusMessage}. From team channel (${chan}${searchHits[0].citation}):\n${searchHits[0].text}`;
      } else {
        synthesizedAnswer = `${this.owner} is currently ${this.statusMessage}. Your question has been delivered to their inbox.`;
      }
    }

    // Send reply back into thread with loop prevention headers
    const to = String(message.to || "").startsWith("#") ? message.to : message.from;
    const thread = message.thread || message.id;
    const receipt = await this.client.send(to, synthesizedAnswer, {
      replyTo: message.id,
      thread,
      title: message.title,
      type: "away-response",
      awayResponse: true,
    });
    this.inbox.sent(receipt, { to, text: synthesizedAnswer, replyTo: message.id, title: message.title });
  }
}
