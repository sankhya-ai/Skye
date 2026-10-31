// Local Search Engine for Chamber.
//
// Built on Node.js native `node:sqlite` with FTS5 (Full-Text Search) and BM25 ranking.
// Runs 100% locally on the engineer's machine with zero external dependencies,
// zero C++ native addons, and zero API keys required.
//
// Keeps Chamber strictly End-to-End Encrypted (AES-256-GCM): the Cloudflare relay
// routes only ciphertext envelopes, while this local engine indexes decrypted talks,
// patches, contracts, team memories, and past incidents.

import { chmodSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { HOME } from "./inbox.mjs";

let sqlite = null;
try {
  sqlite = await import("node:sqlite");
} catch {
  // Graceful fallback if node:sqlite is restricted or unavailable
}

const STOPWORDS = new Set([
  "a", "about", "an", "and", "are", "as", "at", "be", "by", "can", "do",
  "for", "from", "how", "in", "is", "it", "me", "my", "of", "on", "or",
  "that", "the", "there", "this", "to", "we", "what", "whats", "which", "you", "your",
]);

function sanitizeFtsQuery(q) {
  if (!q || typeof q !== "string") return "";
  const allWords = q
    .replace(/[^\w\s\-_./]/g, " ")
    .split(/\s+/)
    .map((w) => w.trim())
    .filter((w) => w.length > 0 && w.length < 80);
  if (!allWords.length) return "";
  const meaningful = allWords.filter((w) => !STOPWORDS.has(w.toLowerCase()));
  const words = meaningful.length ? meaningful : allWords;
  // Use OR matching in FTS5 so natural questions rank best hits with BM25
  return words.map((w, idx) => {
    const escaped = w.replace(/"/g, '""');
    return idx === words.length - 1 && escaped.length >= 3 ? `"${escaped}"*` : `"${escaped}"`;
  }).join(" OR ");
}

export class ChamberSearch {
  constructor(owner = "default", { baseDir = HOME, inMemory = false } = {}) {
    this.owner = owner;
    this.inMemory = inMemory;
    this.dbPath = inMemory ? ":memory:" : join(baseDir, owner, "search.db");
    this.db = null;
    this.fallbackDocs = inMemory || !sqlite ? [] : null;
    this.init();
  }

  init() {
    if (!sqlite?.DatabaseSync) {
      this.fallbackDocs = [];
      if (!this.inMemory) {
        console.warn(`[ChamberSearch] Warning: node:sqlite not available on Node ${process.version}. Running in-memory; search will not persist across restarts. Upgrade to Node 22.13+ for persistent SQLite FTS5 search.`);
      }
      return;
    }

    try {
      if (!this.inMemory) {
        const dir = dirname(this.dbPath);
        mkdirSync(dir, { recursive: true, mode: 0o700 });
        try { chmodSync(dir, 0o700); } catch {}
      }
      this.db = new sqlite.DatabaseSync(this.dbPath);
      this.db.exec("PRAGMA journal_mode = WAL;");
      this.db.exec("PRAGMA busy_timeout = 5000;");

      if (!this.inMemory && existsSync(this.dbPath)) {
        try { chmodSync(this.dbPath, 0o600); } catch {}
      }

      this.db.exec(`
        CREATE TABLE IF NOT EXISTS search_docs (
          id TEXT PRIMARY KEY,
          talk_id TEXT NOT NULL DEFAULT '',
          message_id TEXT NOT NULL DEFAULT '',
          sender TEXT NOT NULL DEFAULT '',
          channel TEXT NOT NULL DEFAULT '',
          type TEXT NOT NULL DEFAULT 'message',
          created_at TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          text TEXT NOT NULL DEFAULT ''
        );
      `);

      this.db.exec(`
        CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
          title,
          text,
          content='search_docs',
          content_rowid='rowid'
        );
      `);

      // Triggers to keep FTS index synchronized with search_docs
      this.db.exec(`
        CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
          INSERT INTO search_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
          INSERT INTO search_fts(search_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS search_docs_au AFTER UPDATE ON search_docs BEGIN
          INSERT INTO search_fts(search_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text);
          INSERT INTO search_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text);
        END;
      `);
    } catch (err) {
      console.warn("Failed to initialize SQLite FTS search, falling back to in-memory:", err.message);
      this.db = null;
      this.fallbackDocs = [];
    }
  }

  /**
   * Index a message, thread, patch, memory, or incident.
   */
  async index({
    id,
    talkId = "",
    messageId = "",
    sender = "",
    channel = "",
    type = "message",
    title = "",
    text = "",
    createdAt = new Date().toISOString(),
  }) {
    if (!id || !text) return;

    const doc = {
      id: String(id),
      talkId: String(talkId || ""),
      messageId: String(messageId || ""),
      sender: String(sender || ""),
      channel: String(channel || ""),
      type: String(type || "message"),
      title: String(title || ""),
      text: String(text).slice(0, 16000),
      createdAt: String(createdAt || new Date().toISOString()),
    };

    if (this.db) {
      try {
        const stmt = this.db.prepare(`
          INSERT INTO search_docs (id, talk_id, message_id, sender, channel, type, created_at, title, text)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            talk_id=excluded.talk_id,
            message_id=excluded.message_id,
            sender=excluded.sender,
            channel=excluded.channel,
            type=excluded.type,
            created_at=excluded.created_at,
            title=excluded.title,
            text=excluded.text
        `);
        stmt.run(
          doc.id,
          doc.talkId,
          doc.messageId,
          doc.sender,
          doc.channel,
          doc.type,
          doc.createdAt,
          doc.title,
          doc.text
        );
        return;
      } catch (err) {
        console.warn("SQLite indexing failed, writing to fallback memory:", err.message);
      }
    }

    if (!this.fallbackDocs) this.fallbackDocs = [];
    const existing = this.fallbackDocs.findIndex((d) => d.id === doc.id);
    if (existing >= 0) this.fallbackDocs[existing] = doc;
    else this.fallbackDocs.push(doc);
  }

  /**
   * Search across indexed documents.
   */
  async search({ query = "", channel = "", type = "", limit = 10 } = {}) {
    if (!query || typeof query !== "string") return [];

    if (this.db) {
      const match = sanitizeFtsQuery(query);
      if (!match) return [];

      try {
        let sql = `
          SELECT d.*, bm25(search_fts) AS rank
          FROM search_fts
          JOIN search_docs d ON search_fts.rowid = d.rowid
          WHERE search_fts MATCH ?
        `;
        const params = [match];

        if (channel) {
          sql += " AND d.channel = ?";
          params.push(channel);
        }
        if (type) {
          sql += " AND d.type = ?";
          params.push(type);
        }

        sql += " ORDER BY rank ASC LIMIT ?";
        params.push(Math.max(1, Math.min(limit, 50)));

        const rows = this.db.prepare(sql).all(...params);
        return rows.map((r) => ({
          id: r.id,
          talkId: r.talk_id,
          messageId: r.message_id,
          sender: r.sender,
          channel: r.channel,
          type: r.type,
          title: r.title,
          text: r.text,
          createdAt: r.created_at,
          score: -r.rank,
          citation: r.talk_id ? `talk/${r.talk_id}#${r.message_id || r.id}` : r.title || r.id,
        }));
      } catch (err) {
        console.warn("FTS query error, falling back to memory search:", err.message);
      }
    }

    // In-memory fallback
    const qLower = query.toLowerCase();
    const words = qLower.split(/\s+/).filter(Boolean);
    const docs = this.fallbackDocs || [];
    const hits = [];

    for (const d of docs) {
      if (channel && d.channel !== channel) continue;
      if (type && d.type !== type) continue;

      const haystack = `${d.title} ${d.text} ${d.sender}`.toLowerCase();
      let matchCount = 0;
      for (const w of words) {
        if (!STOPWORDS.has(w) && haystack.includes(w)) matchCount++;
      }
      if (matchCount > 0) {
        hits.push({
          ...d,
          score: matchCount / Math.max(1, words.length),
          citation: d.talkId ? `talk/${d.talkId}#${d.messageId || d.id}` : d.title || d.id,
        });
      }
    }

    hits.sort((a, b) => b.score - a.score);
    return hits.slice(0, limit);
  }

  /**
   * Specialized incident search: "Have we seen this error or contract before?"
   * Finds past root causes, bug fixes, schema agreements, and responsible agents.
   */
  async incidentMemory(errorSnippet, limit = 5) {
    if (!errorSnippet) return [];
    const hits = await this.search({ query: errorSnippet, limit: limit * 2 });
    return hits
      .filter(
        (h) =>
          h.type === "incident" ||
          h.type === "decision" ||
          h.type === "patch" ||
          /fix|bug|error|cause|contract|schema|patch|failure|exception/i.test(h.text)
      )
      .slice(0, limit);
  }

  close() {
    if (this.db) {
      try {
        this.db.close();
      } catch {}
      this.db = null;
    }
  }
}
