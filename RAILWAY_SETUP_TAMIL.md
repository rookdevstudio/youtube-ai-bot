# GitHub → Railway manual setup

இந்த guide 1 October 2026-ல் project-இன் தற்போதைய code-க்கு தயாரிக்கப்பட்டது. Deployment files தயார்; உங்கள் Railway account-ல் deployment இன்னும் செய்யப்படவில்லை. Local regression/persistent-storage tests pass. இந்த computer-ல் Docker கிடைக்காததால் Linux image build இங்கே ஓட்டப்படவில்லை.

## 1. GitHub-ல் upload செய்ய வேண்டியது

Project folder-ல் உள்ள `github-upload` folder-ஐத் திறக்கவும். **அதற்குள் உள்ள files மற்றும் templates folder மட்டும்** GitHub repo-க்கு upload செய்யவும். `github-upload` என்ற parent folder-ஐ repo-க்குள் வைத்து விடாதீர்கள்.

`github-upload.zip` பயன்படுத்தினால் முதலில் Extract செய்யவும்; ZIP file-ஐ அப்படியே GitHub-ல் upload செய்தால் code deploy ஆகாது. Extract ஆன files-ஐ மேலே சொன்னபடி repo root-ல் upload செய்யவும்.

Repo-வின் முதல் பக்கத்தில் இந்த அமைப்பு இருக்க வேண்டும்:

```text
youtube-ai-bot/
├── Dockerfile
├── .dockerignore
├── .gitignore
├── .env.example
├── Procfile
├── start.py
├── main.py
├── youtube_service.py
├── ai_service.py
├── media_tools.py
├── settings_store.py
├── runtime_config.py
├── requirements.txt
├── requirements-lock.txt
├── README.md
├── RAILWAY_SETUP_TAMIL.md
└── templates/
    ├── index.html
    └── login.html
```

GitHub → **New repository** → பெயர் `youtube-ai-bot` → **Private** → Create. பின்னர் **Add file → Upload files**. மேலே உள்ள files-ஐ upload செய்து **Commit changes** அழுத்தவும். Windows hidden files தெரியாவிட்டால் Explorer → View → Show → Hidden items ON செய்து `.dockerignore`, `.gitignore`, `.env.example` இருப்பதைச் சரிபார்க்கவும்.

**Upload செய்யக் கூடாத files:** `.env`, `client_secrets.json`, `token.json`, `token.pickle`, `.venv`, `__pycache__`, `ffmpeg.exe`, `ffprobe.exe`, `bot_settings.json`, `cached_*.json`, `clips_history.json`, `draft_queue.json`, `replied_comments.json`, `live_reply_receipts.json`, `youtube_connection.json`, screenshots/logs மற்றும் `prepared_shorts`.

GitHub browser upload-ல் `.gitignore` உங்களுக்குப் பதிலாக files-ஐத் தவிர்க்காது. அதனால் தயார் செய்த `github-upload` folder உள்ளடக்கத்தை மட்டும் upload செய்யுங்கள். Secret values code-ல் paste செய்ய வேண்டாம்.

## 2. Railway project உருவாக்குவது

1. https://railway.com-ல் login செய்யவும்.
2. **New Project → Deploy from GitHub repo**.
3. GitHub access கொடுத்து உங்கள் `youtube-ai-bot` repo-ஐத் தேர்வு செய்யவும்.
4. உருவான bot service-ஐத் திறக்கவும். Source branch உங்கள் uploaded branch (`main` என்றால் `main`).
5. Root Directory repo root-ஆக இருக்க வேண்டும்; `Dockerfile` அந்த இடத்தில் இருக்க வேண்டும்.

Railway repo root-ல் உள்ள `Dockerfile`-ஐ கண்டறிந்து build செய்யும். அது Python, Linux FFmpeg/FFprobe, Node மற்றும் dependencies install செய்கிறது. Windows `.exe` files தேவையில்லை. [Railway Docker builds](https://docs.railway.com/builds)

## 3. Persistent Volume கட்டாயமாக சேர்க்கவும்

Bot service-க்கு **Volume** attach செய்யவும். Railway project canvas-ல் service context menu அல்லது **New → Volume** வழியாக உருவாக்கி, bot service-க்கு attach செய்யலாம். UI version-ஐப் பொறுத்து இடம் மாறலாம்; இணைக்கப்படும் service உங்கள் bot service என்பதை உறுதி செய்யவும்.

**Mount Path:**

```text
/data
```

Available plan capacity-க்குள் volume size தேர்வு செய்யுங்கள்; Hobby plan-ல் 1 GB initial state storage போதும் என்ற ஆரம்பத் தேர்வு செய்யலாம். தேவைக்கேற்ப size மாற்றுங்கள். Source download/conversion temporary files container-ன் temporary disk-ல் இருக்கும்; permanent state `/data`-ல் இருக்கும்.

Volume-ஐ `/app`-ல் mount செய்ய வேண்டாம்; அந்த இடத்தில் application code உள்ளது. Volume-ஐ `/data`-ல் attach செய்து mount path-ஐ save/apply செய்யவும். [Railway Volumes](https://docs.railway.com/volumes)

இந்த folder-ல் bot தானாக சேமிப்பவை: YouTube login token, connection state, posting settings, clip history, drafts, reply receipts, caches மற்றும் UI-ல் மாற்றிய Gemini key/model (`/data/settings.env`). Restart/redeploy பிறகும் அதே volume attach இருந்தால் தொடரும். Volume backup வசதியையும் பயன்படுத்தலாம்; volume delete செய்தால் இந்த state அழியும்.

## 4. Railway Variables சேர்க்கவும்

**Bot service → Variables → New Variable** வழியாக ஒவ்வொரு பெயர்/value-யையும் சேர்க்கவும். **Raw Editor** இருந்தால் கீழேயுள்ள block-ஐ paste செய்து placeholder values-ஐ மாற்றலாம். Variable பெயர்கள் capital letters-ல் அப்படியே இருக்க வேண்டும்.

```dotenv
ADMIN_PASSWORD=REPLACE_WITH_YOUR_STRONG_LOGIN_PASSWORD
SESSION_SECRET=REPLACE_WITH_A_LONG_RANDOM_SECRET
BOT_DATA_DIR=/data
BOT_TIMEZONE=Asia/Kolkata
BOT_AUTOMATION_ENABLED=0
BOT_OFFLINE=0
FORWARDED_ALLOW_IPS=*
YOUTUBE_CLIENT_ID=REPLACE_WITH_YOUR_WEB_OAUTH_CLIENT_ID
YOUTUBE_CLIENT_SECRET=REPLACE_WITH_MATCHING_OAUTH_CLIENT_SECRET
```

| Variable | என்ன value? |
|---|---|
| `ADMIN_PASSWORD` | Railway bot login password; நீங்களே வைக்கும் வலுவான password. |
| `SESSION_SECRET` | குறைந்தது 32 random characters. இதை அடிக்கடி மாற்ற வேண்டாம்; மாற்றினால் existing dashboard sessions முடியும். |
| `BOT_DATA_DIR` | `/data`; attached volume mount path-க்கும் இதே value. |
| `BOT_TIMEZONE` | `Asia/Kolkata`; இந்திய நேரப்படி scheduling. |
| `BOT_AUTOMATION_ENABLED` | Setup முடியும்வரை `0`; பின்னர் `1` செய்து deploy செய்யவும். |
| `BOT_OFFLINE` | `0`; saved YouTube connection load ஆக வேண்டும். |
| `FORWARDED_ALLOW_IPS` | Railway proxy deployment-க்கு `*`; Dockerfile-லும் இது உள்ளது. HTTPS login/cookies மற்றும் request URLs சரியாக அமையும். |
| `YOUTUBE_CLIENT_ID` | Google OAuth **Web application** client ID; பொதுவாக `.apps.googleusercontent.com` என்று முடியும். |
| `YOUTUBE_CLIENT_SECRET` | அதே OAuth client-ன் secret. |
| `PUBLIC_BASE_URL` | அடுத்த படியில் Generate Domain செய்த URL: `https://YOUR-SERVICE.up.railway.app`; கடைசியில் `/` வேண்டாம். |

Gemini key/model-ஐ **bot Settings page-ல்** paste/select செய்து save செய்யலாம்; ஆரம்ப Railway variables-ல் கொடுக்க வேண்டும் என்றால் இவை optional:

```dotenv
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GEMINI_MODEL=YOUR_AVAILABLE_GEMINI_TEXT_MODEL
```

`GEMINI_MODEL` value தெரியாவிட்டால் Gemini variables இரண்டையும் ஆரம்பத்தில் விடுங்கள். App default model பயன்படுத்தும்; Settings → key paste → Load / Refresh Models → select → Test & Save மூலம் தேர்வு செய்யுங்கள்.

Dashboard-ல் save ஆன key/model volume-ல் இருக்கும். அடுத்த redeploy-ல் அந்த saved values, ஆரம்ப Railway Gemini variables-ஐ விட முன்னுரிமை பெறும். தொடர்ந்து key/model மாற்ற bot Settings-ஐப் பயன்படுத்துங்கள்.

`PORT` value Railway தானாக வழங்கும்; code-ல் port மாற்ற வேண்டாம். நீங்களே `PORT` அமைத்தால் public domain target port-க்கும் அதே value பயன்படுத்த வேண்டும். `.env` file-ஐ GitHub-ல் upload செய்ய வேண்டாம்.

Random SESSION_SECRET உருவாக்க உங்கள் computer-இல் project PowerShell-ல்:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

அதில் கிடைக்கும் value-ஐ Railway `SESSION_SECRET` variable-ல் paste செய்யவும்.

## 5. Railway Build / Start / Network settings

**Bot service → Settings**:

| Setting | Value |
|---|---|
| Builder | Dockerfile; repo root-ல் `Dockerfile`. |
| Custom Build Command | Blank; Dockerfile dependencies install செய்கிறது. |
| Custom Start Command | `python start.py` |
| Healthcheck Path | `/healthz` |
| Healthcheck Timeout | `300` seconds |
| Restart Policy | `Always` கிடைத்தால் தேர்வு செய்யுங்கள். |
| Replicas | `1` |
| Serverless / App Sleep | **OFF**; automatic scheduler தொடர்ந்து இயங்க வேண்டும். |
| Cron schedule | Blank; bot-க்குள் scheduler உள்ளது. |

`python start.py` command-ஐ **Railway Settings → Deploy → Custom Start Command** field-ல் வையுங்கள். அதை `main.py` அல்லது வேறு code file-க்குள் paste செய்ய வேண்டாம். Dockerfile-ன் CMD ஏற்கனவே அதையே செய்கிறது; field blank இருந்தாலும் Docker CMD பயன்படுத்தப்படும். `run.bat` என்பது Windows local run-க்கு மட்டுமே.

Start file தானாக Railway-ன் `PORT` value படித்து `0.0.0.0`-ல் ஒரு worker-ஆக run செய்கிறது. பல replicas/workers வைத்தால் duplicate background actions வரலாம்; `1` வைத்திருக்கவும். [Railway Start Command](https://docs.railway.com/deployments/start-command), [Healthchecks](https://docs.railway.com/deployments/healthchecks), [Serverless](https://docs.railway.com/deployments/serverless)

Variables மற்றும் volume மாற்றங்களை **Apply / Deploy** செய்யவும். Logs-ல் `Application startup complete` வந்தால் service தொடங்கியுள்ளது.

பின்னர் **Settings → Networking → Public Networking → Generate Domain**. கிடைக்கும் `https://...up.railway.app` URL-ஐ copy செய்து Variables-ல் `PUBLIC_BASE_URL` value-ஆக சேர்த்து மீண்டும் deploy/apply செய்யவும். அந்த URL-ல் `/healthz` திறந்தால் `{"status":"ok"}` வர வேண்டும். [Railway GitHub deployment](https://docs.railway.com/quick-start)

## 6. YouTube Connect வேலை செய்ய Google redirect URL சேர்க்கவும்

Settings-ல் Google Client ID / Secret fields இல்லை. ஆனால் YouTube account connect செய்ய **server-க்கு ஒரு Google OAuth web client அடையாளம் தேவை**. அதை Railway Variables-ல் ஒருமுறை configure செய்ய வேண்டும்; தினசரி bot user அந்த fields-ஐ பார்க்க வேண்டியதில்லை.

ஏற்கனவே உங்கள் local bot-இன் `.env` file-ல் `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` இருந்தால் அவற்றை Railway Variables-க்கு copy செய்யலாம். அவை `.env`-ல் இல்லாவிட்டால் local `client_secrets.json`-இன் `web.client_id` / `web.client_secret` values-ஐப் பார்க்கவும். அந்த file-ஐ GitHub-ல் upload செய்ய வேண்டாம். `installed` client மட்டும் இருந்தால் Google-ல் Web application client உருவாக்கவும்.

1. https://console.cloud.google.com-ல் அந்த OAuth client இருக்கும் project-ஐத் தேர்வு செய்யவும். அந்த project-ஐ edit செய்ய உங்களிடம் access இருக்க வேண்டும்.
2. **APIs & Services → Library → YouTube Data API v3 → Enable**.
3. **Google Auth Platform → Clients** அல்லது **APIs & Services → Credentials → OAuth 2.0 Client IDs**.
4. Railway Variables-ல் போட்ட அதே Web application client-ஐத் திறக்கவும்.
5. **Authorized redirect URIs → Add URI**:

```text
https://YOUR-SERVICE.up.railway.app/auth/callback
```

உங்கள் உண்மையான Railway domain-ஐ பயன்படுத்தவும். `https`, domain, `/auth/callback` அனைத்தும் சரியாக இருக்க வேண்டும். Local URI `http://localhost:8000/auth/callback` இருந்தால் அதை வைத்துக்கொண்டு புதிய Railway URI-யை கூடுதலாக சேர்க்கலாம்.

6. Save செய்யவும். OAuth app Testing நிலையில் இருந்தால் Audience/Test users பகுதியில் உங்கள் channel-ன் Google account-ஐ சேர்க்கவும்.
7. Railway bot URL → Login → Settings → **Connect YouTube** → சரியான channel/account தேர்வு செய்து permissions கொடுக்கவும்.

`redirect_uri_mismatch` என்றால் `PUBLIC_BASE_URL` மற்றும் Google redirect URI ஒரே domain-ஆக உள்ளதா பார்க்கவும். [Google web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)

External OAuth app **Testing** நிலையில் இருந்தால் YouTube scopes பயன்படுத்தும் refresh token 7 நாட்களில் expire ஆகலாம். நீண்ட கால unattended run-க்கு Google-ன் production/verification தேவைகளைச் சரிபார்க்கவும்; Testing நிலையில் இருந்தால் reconnect தேவைப்படலாம். [Google OAuth token expiry](https://developers.google.com/identity/protocols/oauth2)

## 7. Gemini மற்றும் automation configure செய்யவும்

1. Railway bot URL-ல் `ADMIN_PASSWORD` கொண்டு login செய்யவும்.
2. **Settings → Gemini API key** paste.
3. **Load / Refresh Models**.
4. Dropdown-ல் model select → **Test & Save**. Key/model test வெற்றியாக வேண்டும். Gemini daily quota இருந்தால் அது reset ஆக வேண்டும் அல்லது உங்கள் API plan/billing-ஐச் சரிபார்க்க வேண்டும்.
5. **Scheduler** tab-ல் தினசரி Shorts count, நேரங்கள் மற்றும் posting mode தேர்வு செய்யவும்.
6. தேவையான toggles ON: **Automatic Shorts Creation**, **Auto-Public: Shorts, Videos & Live**, தேவையெனில் comments/live replies.
7. **Save Timing Settings & Apply**.
8. Railway Variables-ல் `BOT_AUTOMATION_ENABLED=1` மாற்றி **Deploy / Apply** செய்யவும்.
9. App-ல் automation running மற்றும் YouTube connected நிலையைப் பார்க்கவும். Settings/key/model மறுபடியும் கேட்கக்கூடாது; volume-ல் save ஆகியிருக்க வேண்டும்.

Auto-Public ON என்றால் connected channel-ன் private/unlisted uploads மற்றும் streams Public ஆக மாற்றப்படும். Future publish schedules அவற்றின் நேரம் வரை காத்திருக்கும். Cloud-ல் முதல் deployment புதிய state-உடன் தொடங்கும்; local `bot_settings.json` GitHub-ல் செல்லாது. Cloud dashboard-ல் settings-ஐ ஒருமுறை தேர்வு செய்ய வேண்டும்.

Railway bot வேலை செய்யத் தொடங்கியபிறகு உங்கள் computer-ல் ஓடும் பழைய bot-ஐ `Ctrl+C` மூலம் நிறுத்தவும். ஒரே channel-க்கு local மற்றும் cloud bot இரண்டும் ஒரே நேரத்தில் automation இயக்க வேண்டாம். Railway ஓடும்போது computer/browser/Codex OFF இருந்தாலும் cloud bot தொடரும்; billing/resource availability மற்றும் external API limits பொருந்தும்.

## 8. எங்கு என்ன வைக்க வேண்டும்?

| பொருள் | வைக்கும் இடம் |
|---|---|
| Python / HTML code | GitHub repo files; upload folder அமைப்பிலேயே. |
| Docker build instructions | Repo root `Dockerfile`; ஏற்கனவே தயார். |
| Start command `python start.py` | Railway service Settings → Deploy → Custom Start Command. |
| Password, session secret, Google OAuth credentials | Railway service Variables. |
| Railway public URL | Railway `PUBLIC_BASE_URL` variable. |
| Google callback URL | Google OAuth client → Authorized redirect URIs. |
| Gemini key/model தினசரி மாற்றம் | Bot Settings page; code edit தேவையில்லை. |
| YouTube tokens/settings/history | Railway `/data` volume; bot தானாக save செய்யும். |

## 9. Error வந்தால்

| Error / நிலை | சரிபார்க்க வேண்டியது |
|---|---|
| Dockerfile not found / build missing files | Repo root-ல் `Dockerfile`, Python files, requirements மற்றும் `templates` இருக்கிறதா? Parent `github-upload` folder-க்குள் மட்டும் upload ஆகிவிட்டதா? |
| App failed to respond / 502 | Start command `python start.py`; Logs; domain target port Railway `PORT`-க்கு இணைந்ததா? |
| Request origin is not allowed | `PUBLIC_BASE_URL` உங்கள் உண்மையான HTTPS domain-ஆக இருக்க வேண்டும்; browser-ல் அதே URL பயன்படுத்தவும். |
| Set ADMIN_PASSWORD | Railway Variables-ல் `ADMIN_PASSWORD` value போட்டு deploy செய்யவும். |
| redirect_uri_mismatch | Google authorized redirect URI = `PUBLIC_BASE_URL` + `/auth/callback`. |
| Access denied / account not allowed | OAuth Testing Test users list-ல் channel Google account; correct Web client/project. |
| Login/settings redeploy பிறகு போய்விட்டது | Volume bot service-க்கு attach ஆனதா; mount `/data`; `BOT_DATA_DIR=/data`? |
| Gemini 429 | API quota; server upgrade இதை மாற்றாது. |
| uploadLimitExceeded | YouTube channel upload limit; புதிய upload accepted ஆகாது. YouTube வழிகாட்டுதல்படி பின்னர் retry செய்யவும். |
| Source access / bot verification error | YouTube data-center downloads சில source-களில் தடுக்கப்படலாம். Browser-ல் source playable என்பதைச் சரிபார்க்கவும். தேவையானால் உங்களுடைய authorized local cookies file-ஐ volume-ல் வைத்து `YTDLP_COOKIE_FILE=/data/cookies.txt` பயன்படுத்தலாம்; cookies GitHub-ல் செல்லக்கூடாது. |
| Automatic creation இல்லை | `BOT_AUTOMATION_ENABLED=1`, Automatic Shorts toggle ON, நேரங்கள் Save, channel/AI ready, குறைந்தது 30 seconds completed source உள்ளதா? |

## 10. புதிய code update செய்யும்போது

Updated files-ஐ GitHub repo-ல் அதே பாதைகளில் replace செய்து commit செய்யவும். GitHub autodeploy enabled என்றால் Railway rebuild செய்யும்; இல்லையெனில் service-ல் Deploy latest commit. அதே `/data` volume attach இருந்தால் saved bot settings/login/Gemini config தொடரும்.

Railway-ல் optional budget alerts மற்றும் usage limits configure செய்து Usage page-ஐப் பார்க்கவும். Hard spending limit அடைந்தால் service நிற்கலாம்; 24/7 run-க்கு budget போதுமானதாக இருக்க வேண்டும். [Railway cost controls](https://docs.railway.com/pricing/cost-control)
