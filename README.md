# Brand Discovery Collector

Proof of concept for collecting data from authorized Facebook Pages and Instagram Professional accounts through Meta's official Graph API. It stores posts, videos/Reels, comments, replies, media references, timestamps, metrics, jobs, cursors, and errors in SQLite.

## Run it

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

The API is available at `http://127.0.0.1:8000`, with OpenAPI documentation at `/docs`.
The test dashboard is at `http://127.0.0.1:8000/ui/`.

The dashboard lets you create a brand, start Meta OAuth, select an authorized Page, run recent sync or historical backfill, inspect posts/comments/jobs/errors, and export CSV. If the Page is linked to an Instagram Professional account, the linked Instagram account is added automatically and can be selected in the collection controls. It is intentionally a same-origin POC interface; it does not replace production authentication or organization-level access control.

Create a brand and authorized account:

```powershell
curl -X POST http://127.0.0.1:8000/brands -H 'Content-Type: application/json' -d '{"name":"Example Co","aliases":["Example"],"keywords":["example.lk"]}'
curl -X POST http://127.0.0.1:8000/accounts -H 'Content-Type: application/json' -d '{"brand_id":1,"platform":"facebook","platform_account_id":"PAGE_ID","token_env_var":"META_PAGE_TOKEN"}'
$env:META_PAGE_TOKEN = 'PASTE_LOCALLY_ONLY'
curl -X POST http://127.0.0.1:8000/accounts/1/collect
curl -X POST 'http://127.0.0.1:8000/accounts/1/collect?backfill=true'
curl http://127.0.0.1:8000/posts
curl 'http://127.0.0.1:8000/export/posts?format=csv' -o posts.csv
```

Use `token_env_var` to select a per-account environment variable. Tokens are never stored in database rows or logs. `META_ACCESS_TOKEN` is the fallback for accounts without a token environment variable.

For OAuth, configure `META_APP_ID`, `META_APP_SECRET`, `META_OAUTH_REDIRECT_URI`, and a Fernet key in `META_TOKEN_ENCRYPTION_KEY`. The POC stores encrypted tokens in `META_TOKEN_VAULT_PATH`, which is ignored by Git; production should replace this file vault with a managed secret store. Start the flow with `GET /auth/meta/start?brand_id=1`. After the callback, submit the returned short-lived `selection_token` and selected Page ID to `POST /auth/meta/select`. Page access tokens are never returned by the API.

## Meta setup

1. Create a Meta developer app and add the Facebook/Instagram products.
2. Configure Facebook Login for Business and add the callback URL from `META_OAUTH_REDIRECT_URI` to Meta's valid OAuth redirect URIs. Set `META_APP_ID`, `META_APP_SECRET`, and, when using a Business Login configuration, `META_LOGIN_CONFIG_ID`.
3. Use the built-in `/auth/meta/start` flow to let a Page administrator authorize the app and select a Page. The backend exchanges the code, discovers managed Pages, and stores the selected Page token in the encrypted vault.
4. Facebook Page reads generally require `pages_show_list` and `pages_read_engagement`; reading user-generated Page content/comments can require `pages_read_user_content`. Public Page access depends on the application's approved Page Public Content Access/Page Public Metadata Access features.
5. Instagram collection here targets Professional accounts (Business or Creator). With Facebook Login, use `instagram_basic`, `pages_show_list`, `pages_read_engagement`, and `instagram_manage_comments` as applicable to the endpoint and approved app access. The Instagram account must be linked to a Page for that login configuration.
6. Request only the permissions the app needs, complete App Review/Advanced Access where Meta requires it, and verify the exact requirements against the current Meta documentation before production use.

This proof of concept does not scrape pages, bypass login/CAPTCHAs, or use consumer Instagram accounts. It does not claim unrestricted Facebook or Instagram keyword search. `/brands/{id}/discover` matches aliases and keywords in content already collected through authorized accounts and returns an explicit unsupported result for unrestricted public search. Hashtag/business discovery can be added as a separate provider after the required Meta feature access is approved.

## Scheduling and webhooks

The background scheduler polls enabled accounts every `COLLECTION_INTERVAL_MINUTES` (default 15). Normal runs start at the newest post page so new activity is visible. Use `POST /accounts/{id}/collect?backfill=true` for an exhaustive historical walk; its cursor is persisted as pages complete. Recent posts are revisited on every scheduled run so late comments and changed engagement can be captured. Rate limits, known transient Graph API errors, network failures, and 5xx errors retry with exponential backoff; invalid credentials mark the connection for reauthorization. Jobs left running during a crash are recovered on the next scheduler run.

Set `META_WEBHOOK_VERIFY_TOKEN` and `META_WEBHOOK_APP_SECRET`, then configure Meta to call `/webhooks/meta`. The endpoint verifies the challenge and `X-Hub-Signature-256`, deduplicates deliveries by body hash, and queues a recent collection for the matching enabled account. Polling remains the reconciliation path because webhook deliveries can be delayed or duplicated. Receiving a webhook does not grant access that the token does not already have.

## Inspect and export

- `GET /posts?platform=facebook`
- `GET /comments`
- `GET /jobs`
- `GET /errors`
- `GET /auth/meta/start?brand_id=1`
- `GET /auth/meta/callback`
- `POST /auth/meta/select`
- `POST /accounts/{id}/disconnect`
- `GET /export/posts?format=json|csv`
- `GET /export/comments?format=json|csv`
- `GET /brands/{brand_id}/discover`

## Tests and the 72-hour experiment

```powershell
pytest
```

The tests use mocked API responses and never represent mock records as real data. For a 72-hour experiment, create one brand, connect only accounts you are authorized to monitor, start the service continuously, leave polling at a known interval, and record the `/jobs` and `/errors` output at the start and end. Export `/posts` and `/comments` at the end, then compare the platform IDs and timestamps against the source accounts. Keep the token local and rotate it after the experiment if it was shared with a test environment.

## Current limitations

**Facebook:** results are limited to Page data permitted by the token, app mode, Page role, and approved Page access features. Some public Page/user-generated content may be unavailable, and fields can vary by Graph API version. Broad monitoring of unrelated Pages requires separate approved access or an authorized provider.

**Instagram:** this collector is for Instagram Professional accounts and uses the account's `/media` edge. Consumer accounts are unsupported. Media and comment fields, metrics, retention, and rate limits depend on the selected Meta login configuration and permissions. Carousel children and nested comments are requested when exposed by the API. Real-time delivery requires a separately configured Meta webhook subscription.

The external mention provider searches only content already collected from authorized accounts. It intentionally reports unrestricted Facebook and Instagram keyword search as unsupported. Validate any third-party provider against real Sri Lankan brand samples before relying on its coverage claims.

