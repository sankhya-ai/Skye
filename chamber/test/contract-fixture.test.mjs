// A real BE/FE migration fixture: the transition plan the contract registry tracks, run as
// actual code. The registry records *claimed* evidence (see its trust note); this fixture is
// what real evidence looks like — an intentional breaking change tested in both rollout
// orders, so an "adapted" ack can cite tests that exist.

import assert from "node:assert/strict";
import { test } from "node:test";

// The backend, before and after the approved change (dual-field transition: the ISO field
// stays, an epoch-seconds field is added; removal would be a separate decision).
const backendV1 = (expiry) => ({ expires_at: expiry.toISOString() });
const backendV2 = (expiry) => ({ expires_at: expiry.toISOString(), expires_at_epoch_seconds: Math.floor(expiry.getTime() / 1000) });

// The frontend, before and after adapting: the old client only knows the ISO field; the
// adapted client prefers epoch seconds and falls back to the ISO field, normalizing to a
// JS Date at the API boundary.
const frontendV1 = {
  tokenExpiry(response) {
    return new Date(response.expires_at);
  },
};
const frontendV2 = {
  tokenExpiry(response) {
    if (response.expires_at_epoch_seconds !== undefined) {
      const seconds = response.expires_at_epoch_seconds;
      assert.ok(Number.isSafeInteger(seconds), "epoch seconds must be a safe integer");
      return new Date(seconds * 1000); // explicit seconds → milliseconds
    }
    return new Date(response.expires_at); // ISO fallback for older backends
  },
};

const EXPIRY = new Date("2026-11-01T10:00:00.000Z");

test("old frontend tolerates the new backend's extra field (additive deploy first)", () => {
  const response = backendV2(EXPIRY); // new backend, old client
  assert.equal(response.expires_at_epoch_seconds !== undefined, true);
  assert.deepEqual(frontendV1.tokenExpiry(response), EXPIRY); // permissive parse, extra field ignored
});

test("adapted frontend works against the old backend (ISO fallback)", () => {
  const response = backendV1(EXPIRY); // old backend, new client
  assert.deepEqual(frontendV2.tokenExpiry(response), EXPIRY);
});

test("adapted frontend prefers epoch seconds, and both fields name the same instant", () => {
  const response = backendV2(EXPIRY);
  assert.equal(response.expires_at_epoch_seconds * 1000, new Date(response.expires_at).getTime());
  assert.deepEqual(frontendV2.tokenExpiry(response), EXPIRY);
});

test("a backend that dropped the ISO field early is caught as a breaking change", () => {
  const breaking = { expires_at_epoch_seconds: Math.floor(EXPIRY.getTime() / 1000) };
  // The v1 client has no guard: an invalid Date is the failure mode.
  assert.ok(Number.isNaN(frontendV1.tokenExpiry(breaking).getTime()), "old client breaks — this is why removal needs its own decision");
  assert.deepEqual(frontendV2.tokenExpiry(breaking), EXPIRY);
});
