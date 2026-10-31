# Chamber identity v2 + v3 (design + as-built semantics — 2026-10-31)

Status: v2 (signed attribution, enrollment certificates) and v3 (per-epoch read keys, atomic
revoke+rotate) are implemented and covered by test/identity-v2.test.mjs and test/identity-v3.test.mjs.
The v3 section below records the AS-BUILT semantics, including the crash behavior, with the test
names that prove each claim. No publication or deployment is authorized.
Scope discipline: signed attribution is brick one; it is NOT per-member read confidentiality, and nothing here
claims it. Read confidentiality lands only with key distribution/rotation (brick three), and stays explicitly
incomplete until then.

## What the current line actually guarantees (verified in source, not assumed)

- lib/protocol.mjs: one invite secret per line -> token = HMAC(secret, "chamber/token"), line id = sha256(token)[:32],
  key = HMAC(secret, "chamber/key") AES-256-GCM. Every member holds the SAME key.
- Content+envelope binding EXISTS: bodies are sealed with (line, sender, to, id, reply_to) as AAD, and the relay
  cannot seal (it never sees the secret), so relay-side forgery or envelope/body swapping fails to open at clients.
- Sender attribution at the relay EXISTS: relay/src/index.js deliver() inserts from_agent = who.agent (the
  CONNECTED socket's claimed name), not the client frame; the names table binds name -> member (pass holder),
  first-come, one name per member, admin removal frees it.
- Personal-invite mode (ADMIN_KEY): an Ed25519-signed pass is checked at connect/member-check; revocation is a
  signed order.

## The real gaps v2 must close

1. POSSESSION: the pass is a bearer token. Anyone holding secret+pass connects AS the member and claims their
   name. There is no per-member secret at all: identity is "holds the shared secret", not "holds THEIR key".
2. MESSAGE-LEVEL PROOF: attribution is the relay's word about a live connection. A hijacked/stolen connection
   (or a relay bug) writes history as that member; nothing a member signed can be verified later, and nothing
   prevents replays of old sealed bodies (id is client-chosen; the relay dedupes only on exact id collision).
3. READ CONFIDENTIALITY: every member can open every body (shared key). The relay routes envelopes per member
   in pass mode, but that is routing policy, not cryptographic read scope. NOT closed by bricks one and two.

## v2 design (opt-in per line, versioned, no silent downgrade)

A line is created v1 (today) or v2; the version is fixed at claim time and stored by the relay. A v2 line
REJECTS legacy clients at connect (clear error: "this chamber needs chamber >= v2"), never downgrades silently.
A v1 line never pretends v2 properties.

- Brick one - member keys + enrollment: each member generates an Ed25519 keypair locally; the private key never
  leaves their machine (file 0600, NOT in the invite, NOT in any prompt/brief). A v2 invite carries the shared
  secret + an admin-signed CERTIFICATE binding member name -> their public key (the admin signs it out-of-band
  when issuing the invite; enrollment authority = the existing admin key). The relay stores member -> public key.
- Brick two - signed envelopes: every v2 message carries the member's Ed25519 signature over
  (line, from, to, id, reply_to, ts, body-hash). The relay VERIFIES the signature against the enrolled key
  before storing/forwarding (it already routes on the envelope; verification is additive). Clients verify too.
  The seal (shared line key) stays for content privacy from the relay in this brick.
- Brick three - read scope (explicitly incomplete in this design round): per-channel/per-DM keys wrapped for
  their member set with the enrolled public keys; membership change re-wraps (rotation). Until this lands, ALL
  v2 copy must say: members can still read everything on the line; v2 proves WHO said it, not WHO may read it.
- Revocation: the admin's signed removal deletes name+key enrollment; the relay rejects that key's signatures
  from that moment. Already-sealed history stays verifiable (public keys are in the enrollment record).

## Test matrix (all required before the brick is claimed)

1. Impersonation/name binding: a message signed by member A's key but claiming from B is rejected by relay AND
   by clients; a v2 client cannot claim a name enrolled to another member.
2. Replay: resending a stored (id, ts, signature) frame is rejected (id seen) ; the same body with a new id but
   an OLD ts outside the acceptance window is rejected.
3. Cross-line replay: a frame valid on line X fails verification on line Y (line id inside the signed payload).
4. Revoked credentials: after the admin's signed removal, that member's signatures are rejected at connect AND
   on send; their pass stops working; an already-connected socket is dropped.
5. Legacy downgrade: a v1 client connecting to a v2 line gets the explicit version error; a v2 client on a v1
   line runs v1 semantics and SAYS so (no v2 claims in UI/copy).
6. Enrollment authority: a certificate signed by a non-admin key is rejected; a certificate for a different
   line is rejected; the private key never appears in the invite text (string-level test) and never leaves the
   machine (no code path serializes it).
7. Key custody: invite/prompt/brief constructors carry name + certificate + public key only.

## Compatibility

Existing clients (Claude Code, Codex, Antigravity, DeepSeek Harness) keep working on v1 lines unchanged.
Root's Python client (server/tura_code/line, byte-compatible with protocol.mjs) needs the same v2 bricks; that
is a separate artifact in the Root repo, coordinated with the mission branch, not bundled here.


## v3 as built: per-epoch read keys and the atomic revoke+rotate

- Per-epoch AES-256-GCM read keys; the epoch number is in the AAD and the signed envelope, so a
  body sealed under one epoch opens under no other. Keys reach members as admin-signed
  distributions on the epoch-0 bootstrap channel, each wrapped (ECIES: X25519 + HKDF-SHA256 +
  AES-GCM) to the member's certificate-bound encryption key. The relay holds ciphertext only.
- The relay tracks a TRUSTED CURRENT EPOCH in Durable Object SQL storage (the meta table;
  pre-existing relays that hold it in KV keep reading it from there). Sends under a stale or
  future epoch are refused, so a revoked holder's retained key never yields NEW ciphertext.
- Revocation and rotation are ONE Durable Object storage transaction (transactionSync): the
  removed_names/revoked/enrolled/names/channels writes and the epoch advance commit together or
  not at all. There is no "revoked but not rotated" state - remaining members can never produce
  new old-epoch ciphertext the revoked holder can open - and no "rotated but not revoked" state.
  Socket drops and roster broadcasts are non-storage effects and run only after the commit.
- Crash behavior, proven by fault injection rather than argued (test 9 of the v3 matrix arms a
  test-only hook - live only on relays started with CHAMBER_TEST_HOOKS - that throws INSIDE the
  transaction): the failed order commits NOTHING (the member is not revoked, the epoch is not
  advanced, old-epoch traffic still flows), the rollback holds across a hard SIGKILL relay
  restart (there is no pending-transition state to resume), and a plain retry of the same signed
  order then commits both halves. The trusted epoch has no JS cache - a cache is state a rollback
  cannot undo, and the first version of exactly that bug was caught by this test.
- The remaining ambiguity is response loss, not state: if the relay commits and the answer is
  lost, the admin cannot tell from the network alone. A repeated order answers 409 naming the
  current epoch - a state confirmation, never a rollback - with the revocation already in force.
- Between a committed rotation and the new epoch's distribution landing, the line is read-only
  for new content (stale-epoch sends refused, new-epoch keys not yet held): fail-closed, brief,
  and resolved by the distribution.
- Limits, stated plainly: rotation is never retroactive (old ciphertext stays open to anyone who
  already held its epoch key); read scope is epoch-granular, so a joiner reads pre-join traffic
  of every epoch they hold; this is key rotation, not cryptographic forward secrecy.
