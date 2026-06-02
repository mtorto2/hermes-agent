# Diet Dashboard social media workflow

Date: 2026-05-27
Branch: `feat/diet-dashboard-social-media-workflow`

## Objective

Build a disciplined Hermes/operator workflow for Diet Dashboard social publishing that can later support Facebook, Instagram, and owned media staging without polluting the Diet Dashboard app codebase.

The workflow should:

1. Draft or receive social content.
2. Present a final payload for Matt's approval.
3. Stage media at an owned public URL when Meta requires one.
4. Publish through Meta APIs only after approval.
5. Verify the result with returned IDs and permalinks.
6. Clean up temporary staging media so video does not become unmanaged storage or bandwidth cost.

## Boundary

This is **Hermes/operator automation**, not Diet Dashboard app runtime.

- Diet Dashboard product repo:
  `/Users/matt/Dropbox/CLIENTS/SAVANT SOFTWARE SYSTEMS/DEV/Diet Dashboard`
- Social posting, media staging, Meta publishing, approvals, and marketing-manager behavior belong in Hermes/local operator tooling.
- Do not add posting, R2, token, or Meta publishing helpers to the Diet Dashboard app repo unless the product requirement explicitly becomes an in-app feature.
- Treat repo organization and social automation as separate workstreams.

## Approval contract before publishing

Before Hermes publishes anything externally, show Matt the final payload and get explicit approval:

```text
Platform(s):
Account(s):
Media URL / local source:
Caption:
Hashtags:
CTA/link behavior:
Publish now or schedule:
Staging retention:
```

Do not publish dictated or voice-created content directly without this final payload approval.

## Meta app and current verified asset wiring

Central app:

```text
Savant Hermes Social Publisher
```

Current macOS Keychain reference for the Graph API Explorer user token:

```bash
security find-generic-password -s 'Savant Hermes Social Publisher' -a 'User Token' -w
```

Security rule: never print, paste, commit, or log access tokens. The command above is allowed only as a lookup reference; callers must redact the value and avoid shell tracing.

Verified on 2026-05-27:

| Asset | Value |
|---|---|
| Facebook Page | `Diet Dashboard` |
| Page ID | `185988771256769` |
| Instagram professional account ID | `17841462848944624` |
| IG connection fields | Present as both `instagram_business_account` and `connected_instagram_account` |

Verified Graph chain:

```text
Savant Hermes Social Publisher app
  -> Matt user token
  -> Diet Dashboard Facebook Page
  -> connected Diet Dashboard Instagram professional account
```

Proof queries used:

```text
GET /me?fields=id,name
GET /me/accounts?fields=id,name,instagram_business_account,connected_instagram_account,access_token
```

The `access_token` field from `/me/accounts` must be redacted in all logs and summaries.

## Recommended media staging architecture

Use Cloudflare R2 or equivalent S3-compatible object storage as a short-lived public fetch dock, not as a permanent video CDN.

Suggested public URL layout:

```text
https://media.dietdashboard.app/permanent/brand/...
https://media.dietdashboard.app/permanent/campaigns/...
https://media.dietdashboard.app/staging/meta/images/YYYY/MM/<slug>.png
https://media.dietdashboard.app/staging/meta/videos/YYYY/MM/<slug>.mp4
```

Default retention rules:

- Images: temporary unless explicitly marked campaign/permanent.
- Videos: temporary staging by default.
- Meta-staging assets: delete after 7-30 days once platform ingestion/publishing is verified.

## Implementation slices

### Slice 1: local media staging helper contract

Create a small helper surface that can be tested without Cloudflare credentials:

```text
diet-media-upload --purpose meta-staging --platform instagram --ttl-days 14 <file>
diet-media-list --expired
diet-media-cleanup --dry-run
diet-media-cleanup --execute
```

The upload helper should emit metadata similar to:

```json
{
  "brand": "diet-dashboard",
  "purpose": "meta-staging",
  "platform": "instagram",
  "media_type": "video",
  "local_source": "/path/to/video.mp4",
  "public_url": "https://media.dietdashboard.app/staging/meta/videos/2026/05/example.mp4",
  "created_at": "2026-05-27T00:00:00Z",
  "ttl_days": 14,
  "delete_after": "2026-06-10T00:00:00Z"
}
```

### Slice 2: Cloudflare R2 integration

After the local helper contract is tested, wire credentials/config via approved secret storage only. Do not commit keys.

Required config values:

```text
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET=diet-dashboard-media
R2_PUBLIC_BASE_URL=https://media.dietdashboard.app
```

### Slice 3: Meta publishing adapter

Keep the Meta adapter narrow and auditable:

- Read the user token from Keychain or another approved secret store.
- Resolve a Page access token from `/me/accounts`.
- Redact access tokens before printing API responses.
- For Instagram, create a media container, poll processing status, publish, then verify permalink/ID.
- For Facebook, upload media directly where the API supports multipart upload.
- Never publish without the approval contract above.

## Tests before implementation

Use TDD for any code-bearing helper:

1. Metadata path and public URL generation.
2. TTL/delete-after calculation.
3. Temporary-vs-permanent purpose rules.
4. Video defaults to temporary staging.
5. Cleanup dry-run does not delete.
6. Cleanup execute deletes only expired staging assets.
7. Missing credentials fail safely before upload.
8. Meta API responses redact token-like fields before logging.

## Non-goals for v1

- No app-code changes in the Diet Dashboard iOS repo.
- No permanent video hosting/streaming platform.
- No automatic publishing without Matt's final approval.
- No scheduled recurring posting until manual publish flow is reliable.
- No broad Cloudflare account automation without explicit approval.
- No credential material in repo docs, chat, shell history, or logs.
