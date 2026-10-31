// Chamber identity v2: a member's OWN Ed25519 key, and what proves a message is theirs.
//
// v1 identity is possession of the shared invite secret: anyone holding secret+pass connects
// AS a member and claims their name, and attribution is the relay's word about a live socket.
// v2 adds a per-member key:
//
//   - the private key is generated here and NEVER leaves this machine: it is written to
//     ~/.chamber/keys/<line>-<owner>.json with mode 0600, and no code path serializes it into
//     an invite, a prompt, a brief, or a message. The invite carries only the admin-signed
//     CERTIFICATE (member name -> public key), never the private key.
//   - enrollment authority is the existing admin key: the admin signs the certificate
//     out-of-band when issuing the invite. The relay stores member -> public key.
//   - every v2 message carries the member's signature over
//     (line, from, to, id, reply_to, ts, sha256(sealed body)); the relay verifies it against
//     the enrolled key before storing or forwarding, and clients verify it again on receipt.
//
// What v2 is NOT: the sealed body still opens with the SHARED line key, so every member can
// still read everything. v2 proves WHO said it, not WHO may read it. Read scope (per-channel
// keys wrapped per member, with rotation) is brick three and stays explicitly incomplete.
//
// The canonical signed payload is mirrored byte-for-byte in relay/src/index.js
// (envelopePayload there): change both or verification breaks.

import { createHash, createPrivateKey, createPublicKey, generateKeyPairSync, sign, verify } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { HOME } from "./inbox.mjs";
import { LineError, OWNER } from "./protocol.mjs";

const b64 = (bytes) => Buffer.from(bytes).toString("base64url");
const unb64 = (text) => Buffer.from(String(text), "base64url");

/** A raw 32-byte Ed25519 public key is 43 base64url chars. */
export const PUBKEY = /^[A-Za-z0-9_-]{43}$/;

/** A raw 32-byte X25519 public key is the SPKI's last 32 bytes (43 base64url chars, like Ed25519). */
function newEncKey() {
  const { publicKey, privateKey } = generateKeyPairSync("x25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  return { public: b64(raw), private: b64(privateKey.export({ format: "der", type: "pkcs8" })) };
}

/**
 * A fresh member key: `public` is the raw 32-byte signing key everyone may see; `private` stays
 * here. A v3 member ALSO gets a separate X25519 keypair (`encPublic`/`encPrivate`) for receiving
 * epoch keys: the signing key is never used for encryption, and the encryption key never signs.
 */
export function newMemberKey() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const enc = newEncKey();
  return { public: b64(raw), private: b64(privateKey.export({ format: "der", type: "pkcs8" })), encPublic: enc.public, encPrivate: enc.private };
}

/** Where a member's private key lives: ~/.chamber/keys/<line>-<owner>.json (mode 0600). */
export function keyPath(lineId, owner) {
  return join(HOME, "keys", `${lineId}-${owner}.json`);
}

/** Save a member key so only this user can read it. The private halves are never written anywhere else. */
export function saveMemberKey(lineId, owner, key) {
  const dir = join(HOME, "keys");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  writeFileSync(
    keyPath(lineId, owner),
    JSON.stringify({ v: 2, line: lineId, owner, public: key.public, private: key.private, ...(key.encPublic ? { encPublic: key.encPublic, encPrivate: key.encPrivate } : {}) }),
    { mode: 0o600 },
  );
  return keyPath(lineId, owner);
}

/** This machine's key for a member of a line, or null when it was never enrolled here. */
export function loadMemberKey(lineId, owner) {
  try {
    const key = JSON.parse(readFileSync(keyPath(lineId, owner), "utf8"));
    if (key?.v === 2 && PUBKEY.test(key.public) && key.private) return key;
  } catch {}
  return null;
}

/**
 * Enroll this machine as `owner` on a line: generate the member key, keep the private half
 * here (0600), and return the PUBLIC half for the admin to certify out-of-band. The admin's
 * certificate (admin.issueCertificate) plus the line's invite secret is the whole invite;
 * the private key is never part of it.
 */
export function enrollOwner(lineId, owner) {
  if (!OWNER.test(String(owner || ""))) throw new LineError("a member name is lowercase, e.g. nitin");
  const existing = loadMemberKey(lineId, owner);
  if (existing?.encPublic) return { public: existing.public, encPublic: existing.encPublic, path: keyPath(lineId, owner), existing: true };
  if (existing) {
    // A v2 key on disk: add the v3 encryption keypair, keep the signing key (certs stay valid).
    const enc = newEncKey();
    saveMemberKey(lineId, owner, { ...existing, encPublic: enc.public, encPrivate: enc.private });
    return { public: existing.public, encPublic: enc.public, path: keyPath(lineId, owner), existing: true };
  }
  const key = newMemberKey();
  saveMemberKey(lineId, owner, key);
  return { public: key.public, encPublic: key.encPublic, path: keyPath(lineId, owner), existing: false };
}

/**
 * Verify a member certificate against the ADMIN's public key (the invite's third segment - the
 * only trust anchor), and bind it to this line and this owner name. Returns the claims, or null:
 * a relay handing out substituted, forged, cross-line, or mis-named certificates fails closed,
 * and messages from that "member" are dropped, never verified against the substituted key.
 */
export function verifyCertificate(adminPub, cert, { line, owner }) {
  const [body, sig] = String(cert || "").split(".");
  if (!body || !sig) return null;
  try {
    const claims = JSON.parse(unb64(body).toString("utf8"));
    if (![2, 3].includes(claims?.v) || claims.line !== line || claims.owner !== owner || !PUBKEY.test(claims.pubkey || "") || !claims.member) return null;
    // v3 certificates also bind the member's X25519 ENCRYPTION key (for epoch-key distribution).
    if (claims.v === 3 && !PUBKEY.test(claims.enc || "")) return null;
    const spkiHeader = Buffer.from("302a300506032b6570032100", "hex");
    const key = createPublicKey({ key: Buffer.concat([spkiHeader, unb64(adminPub)]), format: "der", type: "spki" });
    // The admin signs the base64url TEXT of the claims (see admin.signed), so verify over that text.
    if (!verify(null, Buffer.from(body), key, unb64(sig))) return null;
    return claims;
  } catch {
    return null;
  }
}

/** The claims of a v2 certificate (`body` of the signed token), or null when `pass` isn't one. */
export function parseCert(pass) {
  const body = String(pass || "").split(".")[0];
  try {
    const claims = JSON.parse(unb64(body).toString("utf8"));
    if ([2, 3].includes(claims?.v) && claims.line && claims.member && claims.owner && PUBKEY.test(claims.pubkey || "")) return claims;
  } catch {}
  return null;
}

/** sha256 of the SEALED body string, hex: the signature signs the hash, not the body. */
export function bodyHash(body) {
  return createHash("sha256").update(String(body), "utf8").digest("hex");
}

/**
 * The exact bytes a v2 signature covers. Mirrored in relay/src/index.js: change both.
 * v3 messages sign the same fields PLUS the epoch (a v3 signature commits to the epoch the body
 * is sealed under, so a relay cannot re-label ciphertext across epochs without breaking the sig).
 */
export function envelopePayload({ line, from, to, id, replyTo, ts, hash, epoch = 0 }) {
  return epoch ? ["chamber-msg-v3", line, from, to, id, replyTo || "", String(ts), String(epoch), hash].join("\n") : ["chamber-msg-v2", line, from, to, id, replyTo || "", String(ts), hash].join("\n");
}

/** Sign a message envelope with the member's private key (which never leaves this machine). */
export function signEnvelope(memberPrivate, fields) {
  const key = createPrivateKey({ key: unb64(memberPrivate), format: "der", type: "pkcs8" });
  return b64(sign(null, Buffer.from(envelopePayload(fields), "utf8"), key));
}

/** Verify a message envelope against the sender's enrolled public key. */
export function verifyEnvelope(memberPublic, fields, sig) {
  try {
    const spkiHeader = Buffer.from("302a300506032b6570032100", "hex");
    const key = createPublicKey({ key: Buffer.concat([spkiHeader, unb64(memberPublic)]), format: "der", type: "spki" });
    return verify(null, Buffer.from(envelopePayload(fields), "utf8"), key, unb64(sig));
  } catch {
    return false;
  }
}
