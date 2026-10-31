// The workers.dev subdomain Chamber registers for a new Cloudflare account must be one Cloudflare accepts,
// whatever the computer's username looks like.

import assert from "node:assert/strict";
import { test } from "node:test";

import { subdomainName } from "../lib/deploy.mjs";

test("a workers.dev subdomain is lowercase, hyphen-trimmed and short enough, whatever the username", () => {
  const valid = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
  for (const username of ["Ashish", "-root-", "First.Last", "a".repeat(100), "dev--", "", "ÜBER"]) {
    const name = subdomainName(username);
    assert.match(name, valid, `${JSON.stringify(username)} gave ${name}`);
  }
  assert.match(subdomainName("Ashish"), /^ashish-[0-9a-f]{4}$/);
  assert.match(subdomainName("!!!"), /^chamber-[0-9a-f]{6}$/);
});
