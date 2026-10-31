// Chamber identity v3, brick three: per-epoch READ keys. v2 proves who said it; v3 scopes who
// can read it. The shared invite key still bootstraps (it seals the admin's epoch-distribution
// messages), but message bodies are sealed with a per-epoch key the admin wraps to each member.
//
//   epoch key  K_e  = 32 random bytes, AES-256-GCM; epoch 1 is created with the chamber, and the
//               admin ROTATES (new epoch) on every revocation. A body is sealed with K_e and its
//               AAD carries the epoch, so ciphertext cannot be replayed across epochs or lines.
//   wrapping        = ECIES with established primitives only: an ephemeral X25519 keypair,
//               ECDH with the member's certified encryption key (the v3 cert's `enc` claim),
//               HKDF-SHA256(salt = line id, info = "chamber/epoch-wrap/v3"), AES-256-GCM with the
//               wrap bound to (line, epoch, owner) as associated data. The member's SIGNING key
//               is never used for encryption; no key-conversion construction anywhere.
//   authority       = the admin's cert-issuing key signs every distribution
//               ("chamber-epoch-v3" / line / epoch / the sorted wraps); clients verify it against
//               the invite's admin public key, so a relay or another member cannot inject keys.
//
// The honest limits, stated wherever v3 is described:
//   - rotation is NOT retroactive: a revoked member keeps every epoch key they already held, so
//     old ciphertext stays readable to them. Rotation protects only what is sent AFTER it.
//   - a late joiner receives the CURRENT epoch only: history from before they joined stays
//     unreadable to them (they read from the epoch they joined at, forward).
//   - the relay still sees ciphertext metadata: who posts, when, how much, and the epoch number.
//   - the static invite key remains a line-wide bootstrap: distribution messages are sealed with
//     it (every invite holder opens them), but the wrapped epoch keys inside open only for the
//     certified recipient's encryption key.

import { createPrivateKey, createPublicKey, diffieHellman, generateKeyPairSync, hkdfSync, sign, verify } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { HOME } from "./inbox.mjs";
import { LineError, OWNER } from "./protocol.mjs";

const b64 = (bytes) => Buffer.from(bytes).toString("base64url");
const unb64 = (text) => Buffer.from(String(text), "base64url");
const enc = new TextEncoder();

/** A fresh epoch key (raw 32 bytes, base64url). */
export function generateEpochKey() {
  return b64(crypto.getRandomValues(new Uint8Array(32)));
}

const wrapKey = (shared, line) => Buffer.from(hkdfSync("sha256", shared, Buffer.from(String(line), "utf8"), Buffer.from("chamber/epoch-wrap/v3", "utf8"), 32));

async function sealAes(keyBytes, data, aad) {
  const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const sealed = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce, additionalData: enc.encode(aad) }, key, data));
  const out = new Uint8Array(12 + sealed.length);
  out.set(nonce);
  out.set(sealed, 12);
  return out;
}

async function openAes(keyBytes, raw, aad) {
  const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["decrypt"]);
  return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12), additionalData: enc.encode(aad) }, key, raw.slice(12)));
}

const wrapAad = (line, epoch, owner) => `${line}|epoch|${epoch}|${owner}`;

/**
 * Wrap the epoch key `keyB64` for one member: ECIES to their certified X25519 public key.
 * Returns { eph, sealed } (both base64url); only the holder of the matching private key unwraps.
 */
export async function wrapEpochKey(encPub, { line, epoch, owner, key }) {
  const eph = generateKeyPairSync("x25519");
  const shared = diffieHellman({ privateKey: eph.privateKey, publicKey: createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b656e032100", "hex"), unb64(encPub)]), format: "der", type: "spki" }) });
  const sealed = await sealAes(wrapKey(shared, line), unb64(key), wrapAad(line, epoch, owner));
  return { eph: b64(eph.publicKey.export({ format: "der", type: "spki" }).subarray(-32)), sealed: b64(sealed) };
}

/** The inverse of wrapEpochKey, for the member holding the private half; null when it doesn't open. */
export async function unwrapEpochKey(encPrivate, wrap, { line, epoch, owner }) {
  try {
    const shared = diffieHellman({ privateKey: createPrivateKey({ key: unb64(encPrivate), format: "der", type: "pkcs8" }), publicKey: createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b656e032100", "hex"), unb64(wrap.eph)]), format: "der", type: "spki" }) });
    return b64(await openAes(wrapKey(shared, line), unb64(wrap.sealed), wrapAad(line, epoch, owner)));
  } catch {
    return null;
  }
}

/** The exact bytes the admin signs over a distribution. Mirrored in root's Python client. */
export function distributionPayload({ line, epoch, wraps }) {
  const flat = Object.keys(wraps)
    .sort()
    .map((owner) => [owner, wraps[owner].eph, wraps[owner].sealed]);
  return ["chamber-epoch-v3", line, String(epoch), JSON.stringify(flat)].join("\n");
}

/** The admin signs a distribution: rotation authority is the cert-issuing key, nothing else. */
export function signDistribution(adminPrivate, distribution) {
  const key = createPrivateKey({ key: unb64(adminPrivate), format: "der", type: "pkcs8" });
  return b64(sign(null, Buffer.from(distributionPayload(distribution), "utf8"), key));
}

/** Verify a distribution against the invite's admin public key (the only trust anchor). */
export function verifyDistribution(adminPub, distribution, sig) {
  try {
    const spkiHeader = Buffer.from("302a300506032b6570032100", "hex");
    const key = createPublicKey({ key: Buffer.concat([spkiHeader, unb64(adminPub)]), format: "der", type: "spki" });
    return verify(null, Buffer.from(distributionPayload(distribution), "utf8"), key, unb64(sig));
  } catch {
    return false;
  }
}

// ----- where epoch keys live on this machine ---------------------------------------------

/** ~/.chamber/keys/<line>-<owner>-epochs.json (0600): every epoch key this member has received. */
export function epochPath(lineId, owner) {
  return join(HOME, "keys", `${lineId}-${owner}-epochs.json`);
}

/** The stored epochs: { current: <int>, keys: { "<epoch>": "<b64url key>" } }. Never throws. */
export function loadEpochs(lineId, owner) {
  try {
    const data = JSON.parse(readFileSync(epochPath(lineId, owner), "utf8"));
    if (data && typeof data === "object" && data.keys && typeof data.keys === "object") return { current: Number(data.current) || 0, keys: data.keys };
  } catch {}
  return { current: 0, keys: {} };
}

/** Record an epoch key this member just unwrapped; the newest epoch becomes the current one. */
export function saveEpoch(lineId, owner, epoch, keyB64) {
  if (!OWNER.test(String(owner || ""))) throw new LineError("a member name is lowercase, e.g. nitin");
  const data = loadEpochs(lineId, owner);
  data.keys[String(epoch)] = keyB64;
  data.current = Math.max(data.current, Number(epoch));
  const dir = join(HOME, "keys");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  writeFileSync(epochPath(lineId, owner), JSON.stringify(data), { mode: 0o600 });
  return data;
}
