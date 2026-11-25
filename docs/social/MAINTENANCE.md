# Keeping the marketing team and connectors working

## What can break, and how we find out

| Thing | Breaks when | Detected by | Fix |
| --- | --- | --- | --- |
| API version pins (`LINKEDIN_VERSION`, `GRAPH_VERSION` in `skye/social/connectors.py`) | Platform sunsets the version (LinkedIn retires old versions on a schedule) | `skye social doctor`, a failing wire-format test after bumping | Change one constant, update the fixture in `tests/test_social.py`, run `doctor` |
| Tokens | Expiry (X access tokens last about 2 hours, LinkedIn about 60 days, Facebook Page tokens from a long-lived login do not expire) | X renews itself before each publish or check using its refresh token (and keeps the rotated one). LinkedIn renews only if the app was given a refresh token; otherwise it reports `kind=auth` and the owner signs in again. `doctor` flags tokens expiring within 7 days | Sign in again from Connections when asked |
| Rate limits | Daily caps (Meta 30 Reels per Page per day, TikTok about 15 posts per creator) | Publish returns `kind=limit` | Retry later; nothing is queued automatically |
| App review | Platform keeps posts private or blocks until approved (YouTube audit, TikTok audit, Meta App Review, LinkedIn Community Management) | Each connector's `review` text, shown in `skye social list` | Owner applies per platform; cannot be automated |
| Request shapes | Platform changes a field | Contract tests pin the exact URL, headers and body per platform | Update code and fixture together |
| Claude layer | Claude Code CLI flags or output change | Existing adapter availability tests, the Mac doctor check | Fix in the adapter, not in marketing code |

## Rules

- One connector per file section, one spec, one `publish`, one `check`. No shared mutable state.
- All network calls go through the injected transport. Tests never hit the network.
- A connector is `implemented` only when its wire format is pinned by a test and its API shape was checked against the platform's own docs. Others are listed as "not yet", never faked.
- Secrets live in `skye.social.json` (mode 0600). The API returns presence and dates only.
- Posting needs an approved backlog item and an owner action. Workers never hold credentials.
- Monthly: run `skye social doctor`, re-read each platform's changelog for the pinned versions, bump pins.

## Known limits (2026-11-25)

- Text posts only. Instagram, YouTube, TikTok and Pinterest are not implemented.
- TikTok: unaudited apps post private only, and TikTok's guidelines exclude tools meant only for your own or your team's accounts, so an audit may not be granted.
- Instagram posts need a public media URL.
- OAuth sign-in exists for X, LinkedIn and Facebook (local callback at http://127.0.0.1:PORT/oauth/callback, which must be registered as the redirect URI in each developer app; X refuses `localhost`). Bluesky and Mastodon use pasted app password or token. No scheduler, no inbox or reply sending.
- OAuth is tested against a fake transport and an in-process fake provider only. It has not run against the real X, LinkedIn or Meta sign-in pages.
