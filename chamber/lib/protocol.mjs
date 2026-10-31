// The line's invite, keys and sealed messages: everything the relay must not see.
//
// An invite is `<relay url>/#<secret>`. The fragment never leaves this machine;
// the relay only learns what is derived from the secret for it:
//
//   token   = HMAC(secret, "chamber/token")   bearer for the relay
//   line id = sha256(token)[:32]                 the relay checks this, statelessly
//   key     = HMAC(secret, "chamber/key")      AES-256-GCM, never sent
//
// A body is sealed with its envelope (line, sender, recipient, id, reply_to) as
// associated data: the relay can route it but not read, alter, or re-attribute
// it. Byte-compatible with root's Python client (server/tura_code/line).

const enc = new TextEncoder();
export const AGENT = /^[a-z0-9][a-z0-9._-]{0,31}\/[a-z0-9][a-z0-9._-]{0,31}$/;
export const OWNER = /^[a-z0-9][a-z0-9._-]{0,31}$/;
export const INLINE_FILE_BYTES = 64 * 1024;
export const MAX_FILE_BYTES = 25 * 1024 * 1024;

export class LineError extends Error {}

const b64url = (bytes) => Buffer.from(bytes).toString("base64url");
const unb64url = (text) => new Uint8Array(Buffer.from(text, "base64url"));

export class Invite {
  /**
   * `pass`: this member's admin-signed pass (personal invites); empty on an open relay.
   * `adminPub`: the admin's Ed25519 public key - the trust anchor a v2 client verifies EVERY
   * member certificate against, so a relay supplying substituted keys or certs fails closed.
   */
  constructor(relay, secret, pass = "", adminPub = "") {
    this.relay = relay.replace(/\/+$/, "");
    this.secret = secret;
    this.pass = pass;
    this.adminPub = adminPub;
  }

  static create(relay) {
    return new Invite(relay, b64url(crypto.getRandomValues(new Uint8Array(32))));
  }

  /** `https://<relay>/#<secret>`, or `…#<secret>~<pass>` for a personal invite. */
  static parse(text) {
    const [relay, key = ""] = String(text).trim().split("#");
    const [secret, pass = "", adminPub = ""] = key.split("~");
    let url;
    try {
      url = new URL(relay);
    } catch {
      url = null;
    }
    if (!url || !/^https?:$/.test(url.protocol) || secret.length < 32) throw new LineError("an invite looks like https://<relay>/#<secret>");
    return new Invite(`${url.protocol}//${url.host}${url.pathname}`, secret, pass, adminPub);
  }

  /**
   * What a person pastes to join: an invite code (`https://<relay>/#<secret>~<pass>`), or the
   * older `curl -fsSL https://<relay>/install | sh -s -- <secret>~<pass>` command.
   */
  static fromInput(text) {
    const input = String(text).trim();
    const curl = /(https?:\/\/[^\s'"|]+?)\/install\b[\s\S]*?sh -s --\s+([A-Za-z0-9._~-]+)/.exec(input);
    if (curl) return Invite.parse(`${curl[1]}/#${curl[2]}`);
    const link = /https?:\/\/[^\s'"]+#[^\s'"]+/.exec(input);
    // Only the key, copied without the relay address in front of it.
    if (!link && /^[A-Za-z0-9_-]{30,}(~[A-Za-z0-9._-]+)?$/.test(input.replace(/\s+/g, ""))) {
      throw new LineError("That invite is missing the start. Ask your admin to copy the whole invite code: it begins with https://");
    }
    return Invite.parse(link ? link[0] : input);
  }

  toString() {
    return `${this.relay}/#${this.secret}${this.pass ? `~${this.pass}` : ""}${this.adminPub ? `~${this.adminPub}` : ""}`;
  }

  /** The same chamber, with a member's pass. */
  withPass(pass) {
    return new Invite(this.relay, this.secret, pass, this.adminPub);
  }

  /** The same chamber, as a v2 invite: the member's certificate plus the admin's public key. */
  withCertificate(cert, adminPub) {
    return new Invite(this.relay, this.secret, cert, adminPub);
  }

  /** Derive the token, line id and key (WebCrypto is async, so this is separate). */
  async ready() {
    if (this.lineId) return this;
    const mac = await crypto.subtle.importKey("raw", unb64url(this.secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const derive = async (label) => new Uint8Array(await crypto.subtle.sign("HMAC", mac, enc.encode(`chamber/${label}`)));
    this.token = b64url(await derive("token"));
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(this.token)));
    this.lineId = [...digest].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
    this.key = await crypto.subtle.importKey("raw", await derive("key"), "AES-GCM", false, ["encrypt", "decrypt"]);
    return this;
  }

  get wsUrl() {
    return `${this.relay.replace(/^http/, "ws")}/v1/lines/${this.lineId}/ws`;
  }

  blobUrl(blobId) {
    return `${this.relay}/v1/lines/${this.lineId}/blobs/${blobId}`;
  }

  syncUrl(since = 0, limit = 100) {
    return `${this.relay}/v1/lines/${this.lineId}/sync?since=${since}&limit=${limit}`;
  }

  coordinationUrl(sub = "") {
    return `${this.relay}/v1/lines/${this.lineId}/coordination${sub ? `/${sub}` : ""}`;
  }

  get auth() {
    return { Authorization: `Bearer ${this.token}` };
  }

  async #seal(data, aad, key = null) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    key ||= this.key;
    const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: enc.encode(aad) }, key, data));
    const out = new Uint8Array(12 + sealed.length);
    out.set(nonce);
    out.set(sealed, 12);
    return out;
  }

  async #open(raw, aad, what, key = null) {
    try {
      return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12), additionalData: enc.encode(aad) }, key || this.key, raw.slice(12)));
    } catch {
      throw new LineError(`${what} that does not open with this line's key`);
    }
  }

  // v3: the epoch joins the associated data, so a body sealed for epoch e cannot be replayed
  // as another epoch's message (and never across lines - the line id leads the AAD as before).
  #aad({ sender, to, id, replyTo }, epoch = 0) {
    return [this.lineId, sender, to, id, replyTo || "", ...(epoch ? [`e${epoch}`] : [])].join("|");
  }

  async #aesKey(rawB64) {
    return crypto.subtle.importKey("raw", unb64url(rawB64), "AES-GCM", false, ["encrypt", "decrypt"]);
  }

  /**
   * Seal a payload. `epoch` = { n, key } seals with the per-epoch key (v3 bodies) instead of the
   * shared invite key; epoch 0/absent is the shared key (v1/v2 bodies, v3 distribution messages).
   */
  async seal(payload, meta, epoch = null) {
    const key = epoch?.key ? await this.#aesKey(epoch.key) : null;
    return b64url(await this.#seal(enc.encode(JSON.stringify(payload)), this.#aad(meta, epoch?.n || 0), key));
  }

  async open(body, meta, epoch = null) {
    const key = epoch?.key ? await this.#aesKey(epoch.key) : null;
    return JSON.parse(new TextDecoder().decode(await this.#open(unb64url(body), this.#aad(meta, epoch?.n || 0), "a message", key)));
  }

  async sealBlob(data, blobId, epoch = null) {
    const key = epoch?.key ? await this.#aesKey(epoch.key) : null;
    return this.#seal(data, `${this.lineId}|blob|${blobId}${epoch?.n ? `|e${epoch.n}` : ""}`, key);
  }

  async openBlob(raw, blobId, epoch = null) {
    const key = epoch?.key ? await this.#aesKey(epoch.key) : null;
    return this.#open(raw, `${this.lineId}|blob|${blobId}${epoch?.n ? `|e${epoch.n}` : ""}`, "a file", key);
  }
}
