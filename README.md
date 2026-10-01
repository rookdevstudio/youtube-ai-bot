# YouTube AI Bot

Start on Windows by opening `run.bat`, then visit http://localhost:8000. Sign in with the `ADMIN_PASSWORD` already configured in `.env`. The launcher uses the project virtual environment and one server process.

The dashboard shows YouTube API values. Missing data is shown as unavailable, and API failures appear in the status panel. Channel statistics count public videos; the authenticated library also includes private and unlisted uploads. Short candidates are inferred from dimensions, duration or #Shorts because the Data API does not return a definitive Shorts flag.

## Railway deployment

Follow [RAILWAY_SETUP_TAMIL.md](RAILWAY_SETUP_TAMIL.md) for manual GitHub upload, Railway settings, variables, persistent storage and Google OAuth redirects. Upload the contents of the prepared `github-upload` folder to the repository root, or extract `github-upload.zip` first. The deployment package contains 18 application/deployment files; local diagnostics, tests, credentials and runtime state are excluded.

Railway uses the root `Dockerfile` and `python start.py`. Attach a volume at `/data` and set `BOT_DATA_DIR=/data` to retain YouTube login, bot settings and saved Gemini key/model across redeploys. Use one replica with sleep disabled. Keep automation disabled until connections and timing settings are configured. The package was locally started and verified with isolated credentials; an actual Railway deployment and Linux Docker build have not yet been performed.

## Configuration

Keep `.env`, `client_secrets.json`, `token.json` and `token.pickle` private. Existing credentials are supported; successful connection migrates credentials to `token.json` without deleting the old file.

- `ADMIN_PASSWORD`: required dashboard password; no built-in default.
- `GEMINI_API_KEY`: required for AI generation.
- `GEMINI_MODEL`: model available to your key. The local configuration has been verified with `gemini-3.5-flash`.
- `BOT_TIMEZONE`: defaults to `Asia/Kolkata`; all time pickers use this timezone.
- `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`, or `client_secrets.json`: Google OAuth client.
- `PUBLIC_BASE_URL`: defaults to `http://localhost:8000`. Register `<PUBLIC_BASE_URL>/auth/callback` in the OAuth client's authorized redirect URIs. Remote hosting requires HTTPS.
- `SESSION_SECRET`: optional stable random secret; without it, restarting the app signs users out.
- `BOT_AUTOMATION_ENABLED=0`: disables scheduled background actions for a read-only preview. Manual actions are still available.
- `BOT_OFFLINE=1`: skips initial YouTube connection for offline testing.
- `YTDLP_COOKIE_FILE`: optional path to a local cookies file for sources that require authentication.
- `BOT_DATA_DIR`: optional persistent state directory; use `/data` with a Railway volume. Local runs default to the project directory. Cloud UI Gemini changes are saved in `settings.env` there.

Open **Settings** to connect/reconnect YouTube, disconnect this bot, or paste a Gemini API key. Click **Load / Refresh Models**, select a Gemini text model from the live list, then **Test & Save**. The bot checks structured JSON output before saving; failed tests preserve the previous key/model. Successful changes apply immediately and survive restart without code changes. Changing the key clears the previous model list. Saved secrets are never displayed. Google OAuth client ID/secret editing is not exposed in the dashboard; the existing server configuration continues to support YouTube login. Disconnect removes both saved token formats and prevents legacy-token reconnection; it does not change channel videos.

Click **Connect / Reconnect YouTube** if the saved login expires. Google consent needs the account owner. Do not run multiple workers or multiple bot instances against the same state files.

## Behavior

- Existing feature settings are preserved. Automatic replies and uploads run only while the server is running and their toggles are enabled.
- Live chat uses YouTube's continuation token and minimum polling interval. Replies are recorded only after a confirmed send. AI failures do not trigger canned replies.
- Comment replies cover the last 24 hours, skip channel-authored comments and existing channel replies, and use the actual parent comment ID.
- Shorts are exactly 30 seconds and use completed full videos or livestream recordings at least 30 seconds long. Active and upcoming broadcasts cannot be used as clip sources. Downloads that require account cookies, region access or source permissions can still fail with an explicit error.
- Clips must pass video validation and vertical conversion before upload. The downloader retries alternate formats, then falls back to downloading the source and cutting the same offset locally. Downloads and processing have time/size bounds. Conversion uses bounded CPU threads and 30 fps, trying 1080×1920 then 720×1280. Failed conversions never upload the original horizontal file.
- Scheduled Shorts are uploaded privately with a UTC publish time converted from the chosen local time. YouTube handles publication even if the bot later stops. Drafts remain tracked if publication cannot be confirmed.
- Automatic creation respects the configured daily slot count. Scheduled mode prepares up to one hour early. Immediate mode creates at the slot. Turn off Automatic Shorts Creation to stop new automatic uploads; existing YouTube schedules remain in YouTube Studio.
- **Auto-Public: Shorts, Videos & Live** publishes private/unlisted uploads, completed recordings and active/upcoming broadcasts. It checks all uploads with pagination. Future `publishAt` schedules remain intact until their chosen time. The current saved toggle is ON, as requested. Turn it OFF and save to stop this behavior.
- Metadata generation uses the source title/description; it does not inspect footage. Content suggestions are labeled as suggestions, not measured audience insights.

YouTube may keep uploads private due to API project restrictions, processing or account limits. The dashboard reports the actual returned visibility instead of guaranteeing publication. Uploads and sends depend on API quota, connectivity, channel permissions and source availability.

## Verification

Run from this folder:

```powershell
.\.venv\Scripts\python.exe -m unittest test_bot test_extended test_improvements test_deployment -v
.\.venv\Scripts\python.exe diagnose.py --ai
```

`diagnose.py` checks channel data and optionally makes one small Gemini request. It does not post or upload.

`verify_integrations.py` reads live chat, generates metadata and downloads/converts a temporary three-second clip. Without flags it never uploads or sends messages. The explicit `--private-upload` option uploads one temporary private diagnostic clip, verifies processing, then deletes only that test video. It requires working external connections.

`browser_check.cjs` verifies a local preview on port 8765, intercepting upload requests. `run_browser_check.py` passes the configured password through the child environment without printing it. The browser script uses the bundled Playwright installation available on this computer.

Tests cover real values vs unavailable states, API errors, library pagination, comment filtering, live polling, duplicate prevention, authentication, cross-site requests, timezones, draft visibility, failed conversion and job lifecycle. A real private upload and cleanup were tested previously. The latest public-upload attempt completed download, conversion and AI metadata, then YouTube rejected it with `uploadLimitExceeded`. Eighteen existing non-public uploads/broadcasts were successfully made public at the user’s request. Automated tests never post live chat or public comments. See TEST_REPORT.md for the current verification results.

Tamil startup and operating instructions: [RUN_GUIDE_TAMIL.md](RUN_GUIDE_TAMIL.md).

## Current upload-limit status

The latest live attempt returned HTTP 400 `uploadLimitExceeded`, which is the channel upload limit, not the missing-Location bug. The bot now explains this error. YouTube recommends trying again in 24 hours; see [YouTube upload errors](https://support.google.com/youtube/answer/10383400?hl=en). No new public Short was accepted during that attempt.

A later local-preparation request also reached Gemini's daily free-tier quota (429). Daily quota errors are reported without repeating requests as if they were transient failures. Wait for quota reset or review your API plan/billing. The ready MP4 is 30.0 seconds at 1080×1920; its accompanying metadata is explicitly labeled as prepared by Codex from the source, rather than a successful Gemini response.

`prepare_short.py` prepares a real 30-second vertical MP4 and AI metadata locally without uploading. `prepared-short-result.json` points to the latest prepared files. `create_verified_short.py --publish` explicitly creates and uploads one real public Short; do not run it repeatedly while the channel limit remains. `apply_public_visibility.py --apply` applies the enabled Auto-Public setting once, without uploading or sending messages.
