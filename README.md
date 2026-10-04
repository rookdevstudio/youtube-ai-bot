# YouTube AI Bot

Start on Windows by opening `run.bat`, then visit http://localhost:8000. Sign in with the `ADMIN_PASSWORD` already configured in `.env`. The launcher uses the project virtual environment and one server process.

The dashboard shows YouTube API values. Missing data is shown as unavailable, and API failures appear in the status panel. Channel statistics count public videos; the authenticated library also includes private and unlisted uploads. Short candidates are inferred from dimensions, duration or #Shorts because the Data API does not return a definitive Shorts flag.

## Railway deployment

Follow [RAILWAY_SETUP_TAMIL.md](RAILWAY_SETUP_TAMIL.md) for manual GitHub upload, Railway settings, variables, persistent storage and Google OAuth redirects. Upload the contents of the prepared `github-upload` folder to the repository root, or extract `github-upload.zip` first. The deployment package contains 18 application/deployment files; local diagnostics, tests, credentials and runtime state are excluded.

Railway uses the root `Dockerfile` and `python start.py`. Attach a volume at `/data` and set `BOT_DATA_DIR=/data` to retain YouTube login, bot settings and saved Gemini key/model across redeploys. Use one replica with sleep disabled. Keep automation disabled until connections and timing settings are configured. The package was locally verified with isolated credentials and deployed successfully on Railway with a Linux Docker build.

## Configuration

Keep `.env`, `client_secrets.json`, `token.json` and `token.pickle` private. Existing credentials are supported; successful connection migrates credentials to `token.json` without deleting the old file.

- `ADMIN_PASSWORD`: required dashboard password; no built-in default.
- `GEMINI_API_KEY`: required for AI generation.
- `GEMINI_MODEL`: model available to your key. The local configuration has been verified with `gemini-3.5-flash`.
- `BOT_TIMEZONE`: defaults to `Asia/Kolkata`; all time pickers use this timezone.
- `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`, or `client_secrets.json`: Google OAuth client.
- `PUBLIC_BASE_URL`: defaults to `http://localhost:8000`. Register `<PUBLIC_BASE_URL>/auth/callback` in the OAuth client's authorized redirect URIs. Remote hosting requires HTTPS.
- `SESSION_SECRET`: optional stable random secret; without it, restarting the app signs users out.
- `BOT_AUTOMATION_ENABLED=0`: initial paused worker state; Scheduler Start/Pause is saved and takes precedence on later restarts. Manual actions remain available.
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
- Clips must pass frame decoding and visible-picture checks before conversion and again before upload. Solid black/white footage, long blank sections and incomplete decoded clips are rejected. Blank source segments are skipped in bounded groups of four; scanning resumes after restart without marking a failed clip as uploaded. Conversion resets timestamps and preserves the full source image if a center crop loses the picture. The downloader retries alternate formats, then falls back to downloading the source and cutting the same offset locally. Downloads and processing have time/size bounds. Conversion uses bounded CPU threads and 30 fps, trying 1080×1920 then 720×1280. Failed conversions never upload the original horizontal file.
- Scheduled Shorts are uploaded privately with a UTC publish time converted from the chosen local time. YouTube handles publication even if the bot later stops. Drafts remain tracked if publication cannot be confirmed.
- Automatic creation respects the configured daily slot count. Scheduled mode prepares up to one hour early. Immediate mode creates at the slot. Turn off Automatic Shorts Creation to stop new automatic uploads; existing YouTube schedules remain in YouTube Studio.
- **Auto-Public: Shorts, Videos & Live** publishes private/unlisted uploads, completed recordings and active/upcoming broadcasts. It checks all uploads with pagination. Future `publishAt` schedules remain intact until their chosen time. Enable this toggle only when all eligible non-public uploads should become public. Turn it OFF and save to stop this behavior.
- Metadata generation uses the source title/description; it does not inspect footage. Content suggestions are labeled as suggestions, not measured audience insights.

YouTube may keep uploads private due to API project restrictions, processing or account limits. The dashboard reports the actual returned visibility instead of guaranteeing publication. Uploads and sends depend on API quota, connectivity, channel permissions and source availability.

## Source download recovery and saved API keys

YouTube can refuse downloads from a cloud server even when the channel's Google connection works. The bot stops repeated format attempts on a sign-in/bot block and explains the recovery. The deployment installs yt-dlp's matching EJS component alongside Node 22.

In **Settings → Source recordings**, select the matching completed channel video and upload its original full recording once (up to 1 GB; 2 GB total saved copies, subject to available volume space). The duration must match the channel video. Manual Shorts use the selected recording; automatic selection prefers the newest video with a saved copy. Cutting, vertical conversion, source-based Gemini metadata and YouTube upload continue through the existing pipeline. Saved recordings are channel-scoped under `BOT_DATA_DIR`, survive restart with the Railway volume, and are never included in the GitHub package. Remove an unused copy in Settings to free space.

In **Settings → Gemini AI**, paste once, load models, select one and **Test & Save**. Reopening the page shows **API key saved — ready to use** and the selected model. The actual key stays hidden on the server; blank input retains it. A failed catalogue refresh does not erase the saved model. Gemini quota and YouTube permission/upload limits still apply.

Admin login and the dashboard remain available when YouTube returns a daily quota error. If Google authorization succeeds but channel verification cannot run, the bot saves a pending authorization and redirects to Settings instead of an error JSON page. It does not use that authorization for a channel until verification succeeds. Daily quota failures pause API requests until the next midnight Pacific reset plus one minute, including across restarts. Channel verification retries automatically without another Google sign-in; pausing automation still leaves login recovery enabled. Statistics that cannot be loaded show Unavailable, and quota does not clear saved posting times or Gemini settings.

An unloaded channel/library is shown as **not loaded yet**, with the local quota reset time. The page checks connection state every 30 seconds and reloads once when verification or library loading completes, preserving the active tab. **Retry channel loading** checks the saved authorization without another Google login; one manual verification is allowed every five minutes, even if the local cooldown is active. Only a real successful channel response clears that cooldown; this cannot bypass Google's quota. Auto-Public reuses the shared 15-minute library cache, skips known future scheduled uploads and invalidates the cache after an actual visibility change, avoiding a full-channel scan every two minutes. Existing cached statistics are labelled as previously fetched when live refresh fails.

## Verification

Run from this folder:

```powershell
.\.venv\Scripts\python.exe -m unittest test_bot test_extended test_improvements test_deployment test_source_recordings -v
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
# Scheduler save and running state

In **Scheduler & 1-Hour Drafts**, clock times, daily count and posting mode save automatically. Wait for **Times saved** before closing. Duplicate or incomplete times show **Not saved** and keep the previous saved schedule. All times use the displayed `BOT_TIMEZONE` (default Asia/Kolkata).

Choose **Automation worker → Running**, enable **Automatic Shorts Creation**, then press **Save Timing Settings & Apply** to start automatic creation. Feature toggles and Start/Pause require this explicit apply; changing a time alone preserves those settings. The saved worker state persists in `BOT_DATA_DIR/bot_settings.json` across restarts. `BOT_AUTOMATION_ENABLED` supplies the initial state only when no saved worker preference exists.

The Shorts worker checks every 30 seconds independently of comment maintenance. Scheduled mode prepares an upload in the hour before the slot. Immediate mode starts processing at the slot; download/render/upload time means publication is later. Failed attempts use persisted retry delays, and confirmed slots are skipped. Retries stop 10 minutes after a missed slot; the last failure remains visible. Pausing stops new automatic work, while an already running job finishes. Existing YouTube scheduled uploads retain their schedule.

The scheduler panel shows paused connections/features, the next posting time, saved originals and the last automatic failure. If YouTube blocks the Railway download, save the matching full original in **Settings → Source recordings** once. That saved source is preferred for later automatic Shorts; saving it clears its retry delay. A Gemini key or a different posting time cannot grant media-download access. YouTube upload limits and scheduled publication still depend on the channel/API.

