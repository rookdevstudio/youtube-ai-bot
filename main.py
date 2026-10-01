import datetime as dt
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from google_auth_oauthlib.flow import Flow
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from youtube_service import YouTubeService, SCOPES
from ai_service import AIService
from settings_store import update_environment
from runtime_config import load_runtime_environment, settings_environment_path
from media_tools import probe_video
from youtube_service import parse_iso_duration

BASE_DIR = Path(__file__).parent
load_runtime_environment(BASE_DIR)
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD','')
GEMINI_KEY = os.getenv('GEMINI_API_KEY','')
ai_service = AIService(GEMINI_KEY) if GEMINI_KEY else None
yt_service = YouTubeService()
scheduler = BackgroundScheduler(timezone=yt_service.tz)
logs_history = deque(maxlen=100)
job_lock = threading.RLock()
active_job = dict(active=False,job_id='',video_title='',status='idle',progress=0,step_index=0,step_text='Waiting to start',video_id='',short_url='',playlist_url='',error='')
signer = URLSafeTimedSerializer(os.getenv('SESSION_SECRET') or secrets.token_urlsafe(48))
oauth_flows = {}
login_attempts = {}
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_SOURCE_STORAGE_BYTES = 2 * MAX_SOURCE_BYTES


@contextmanager
def configuration_change():
    with job_lock:
        if active_job['active'] or not yt_service.creation_lock.acquire(blocking=False):
            raise ValueError('Wait for the current Short to finish before changing connections.')
    try:
        yield
    finally:
        yt_service.creation_lock.release()


def add_log(message):
    logs_history.append(f'[{dt.datetime.now(yt_service.tz):%H:%M:%S}] {message}')


def update_job_status(step_index,progress,status,step_text,video_id='',short_url='',error=''):
    with job_lock:
        active_job.update(step_index=step_index,progress=progress,status=status,step_text=step_text,error=error)
        if video_id:
            active_job['video_id'] = video_id
        if short_url:
            active_job['short_url'] = short_url
        if status in ('completed','error'):
            active_job['active'] = False
        if yt_service.cached_playlist_id:
            active_job['playlist_url'] = f'https://www.youtube.com/playlist?list={yt_service.cached_playlist_id}'


def run_safely(label,fn,*args):
    try:
        fn(*args)
    except Exception as exc:
        add_log(yt_service.report_error(label,exc))


def fast_live_chat_engine():
    if yt_service.settings.get('automation_enabled'):
        run_safely('Live engine',yt_service.check_and_reply_live,ai_service,add_log)


def periodic_engine():
    if not yt_service.settings.get('automation_enabled'):
        return
    for label,fn,args in (
        ('Draft sync',yt_service.auto_publish_due_drafts,(add_log,)),
        ('Comments',yt_service.check_and_quick_reply_comments,(ai_service,add_log)),
        ('Privacy guard',yt_service.check_and_update_stream_privacy,(30,add_log))):
        if yt_service.youtube:
            run_safely(label,fn,*args)


def scheduled_creation(fn,*args,**kwargs):
    with job_lock:
        if active_job['active'] or yt_service.creation_lock.locked():
            return False
        job_id = secrets.token_hex(12)
        active_job.update(active=True,job_id=job_id,video_title=args[1],status='starting',progress=0,step_index=1,
                          step_text='Automatic scheduled Short…',video_id='',short_url='',playlist_url='',error='')
    def report(*values,**keywords):
        with job_lock:
            if active_job['job_id']==job_id:
                update_job_status(*values,**keywords)
    try:
        return fn(*args,progress_fn=report,**kwargs)
    finally:
        with job_lock:
            if active_job['job_id']==job_id and active_job['active']:
                report(0,0,'error','Automatic Short stopped before completion.',error='Check Activity for the failure reason.')


def short_scheduler_engine():
    if yt_service.settings.get('automation_enabled'):
        run_safely('Short scheduler',yt_service.check_and_prestage_1h_drafts,ai_service,add_log,scheduled_creation)


@asynccontextmanager
async def lifespan(app):
    if os.getenv('BOT_OFFLINE') != '1':
        await __import__('asyncio').to_thread(yt_service.load_saved_credentials)
    scheduler.add_job(fast_live_chat_engine,'interval',seconds=6,max_instances=1,coalesce=True,id='live',replace_existing=True)
    scheduler.add_job(periodic_engine,'interval',seconds=120,max_instances=1,coalesce=True,id='periodic',replace_existing=True)
    scheduler.add_job(short_scheduler_engine,'interval',seconds=30,max_instances=1,coalesce=True,id='shorts',replace_existing=True)
    scheduler.start()
    add_log('Worker ready. Automation '+('running.' if yt_service.settings.get('automation_enabled') else 'paused; start it in Scheduler.'))
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)
templates = Jinja2Templates(directory=str(BASE_DIR/'templates'))


def local_schedule_time(value):
    try:
        target = dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if target.tzinfo is None:
            raise ValueError('Schedule has no timezone.')
        return target.astimezone(yt_service.tz).strftime('%d %b %Y, %H:%M')
    except (AttributeError,ValueError):
        return 'Unknown schedule; check YouTube Studio'


templates.env.filters['local_schedule_time'] = local_schedule_time


def check_auth(request):
    try:
        return signer.loads(request.cookies.get('auth_session',''),salt='login',max_age=43200) == 'admin'
    except (BadSignature,SignatureExpired):
        return False


@app.middleware('http')
async def protect_routes(request,call_next):
    public = request.url.path in ('/login','/healthz','/favicon.ico')
    if not public and not check_auth(request):
        if request.method != 'GET' or request.url.path.startswith('/api/'):
            return JSONResponse({'error':'Sign in to continue.'},status_code=401)
        return RedirectResponse('/login',status_code=303)
    # Browser cross-site form submissions carry Origin; reject them before any mutation.
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin = request.headers.get('origin')
        expected = os.getenv('PUBLIC_BASE_URL') or str(request.base_url).rstrip('/')
        if origin and origin.rstrip('/') != expected.rstrip('/'):
            return JSONResponse({'error':'Request origin is not allowed.'},status_code=403)
    if request.url.path == '/api/settings/source-recording':
        try:
            content_length = int(request.headers.get('content-length','0'))
        except ValueError:
            return JSONResponse({'error':'Invalid upload length.'},status_code=400)
        if content_length > MAX_SOURCE_BYTES + 1024 * 1024:
            return JSONResponse({'error':'Recording exceeds the 1 GB upload limit.'},status_code=413)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@app.exception_handler(Exception)
async def app_error(request,exc):
    message = yt_service.report_error('Request failed',exc)
    add_log(message)
    return JSONResponse({'status':'error','error':message},status_code=502)


@app.exception_handler(ValueError)
async def validation_error(request,exc):
    return JSONResponse({'status':'error','error':str(exc)},status_code=400)


@app.get('/healthz')
def health(details:bool=False):
    result = {'status':'ok'}
    if details:
        result['release'] = '2026.10.02-scheduler-persistence'
    return result


@app.get('/favicon.ico',include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get('/api/settings/status')
def connection_status():
    return dict(youtube_connected=bool(yt_service.youtube and yt_service.channel_id),
                channel_name=yt_service.channel_name,ai_configured=bool(ai_service),
                model=ai_service.model if ai_service else os.getenv('GEMINI_MODEL','gemini-3.5-flash'),
                saved_source_ids=yt_service.saved_source_ids())


@app.post('/api/settings/source-recording')
def save_source_recording(video_id:str=Form(...), recording:UploadFile=File(...)):
    try:
        with configuration_change():
            if not yt_service.youtube:
                raise ValueError('Connect YouTube before uploading a source recording.')
            destination = yt_service.source_recording_path(video_id)
            source = yt_service._source(video_id)
            expected = parse_iso_duration(source.get('contentDetails',{}).get('duration',''))
            if expected < 30:
                raise ValueError('Choose a completed video at least 30 seconds long.')
            destination.parent.mkdir(parents=True,exist_ok=True)
            root = Path(yt_service.base_dir)/'source_recordings'
            used = sum(p.stat().st_size for p in root.rglob('*.mp4'))
            previous = destination.stat().st_size if destination.exists() else 0
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent,suffix='.mp4',delete=False) as file:
                    temporary = Path(file.name)
                    size = 0
                    while chunk := recording.file.read(1024*1024):
                        size += len(chunk)
                        if size > MAX_SOURCE_BYTES:
                            raise ValueError('Recording exceeds the 1 GB upload limit.')
                        if used - previous + size > MAX_SOURCE_STORAGE_BYTES:
                            raise ValueError('Saved recordings exceed the 2 GB storage limit. Remove an unused recording first.')
                        file.write(chunk)
                _, duration = probe_video(temporary)
                if abs(duration - expected) > max(3,expected * .001):
                    raise ValueError('Upload the full original recording for the selected video; its duration must match the channel video.')
                os.replace(temporary,destination)
                yt_service.clear_source_retries(video_id)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
    finally:
        recording.file.close()
    add_log('Full source recording validated and saved for '+video_id+'.')
    return {'status':'ok','message':'Full recording saved. Retry Short creation; this recording will be used automatically, including after a restart.'}


@app.post('/api/settings/remove-source-recording')
def remove_source_recording(video_id:str=Form(...)):
    with configuration_change():
        path = yt_service.source_recording_path(video_id)
        path.unlink(missing_ok=True)
    return {'status':'ok','message':'Saved source copy removed. Your YouTube video is unchanged.'}


def settings_api_key(api_key):
    key = api_key.strip() or GEMINI_KEY
    if not key or any(c.isspace() for c in key) or len(key)>512:
        raise ValueError('Paste a valid Gemini API key.')
    return key


@app.post('/api/settings/gemini/models')
def gemini_models(api_key:str=Form('')):
    candidate = AIService(settings_api_key(api_key))
    try:
        return {'models':candidate.list_supported_models()}
    finally:
        candidate.client.close()


@app.post('/api/settings/gemini')
def save_gemini(api_key:str=Form(''),model:str=Form('')):
    global ai_service, GEMINI_KEY
    key = settings_api_key(api_key)
    model = model.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,119}',model):
        raise ValueError('Enter a valid Gemini model name.')
    with configuration_change():
        candidate = AIService(key,model=model)
        try:
            if model not in {item['id'] for item in candidate.list_supported_models()}:
                raise ValueError('Select a model available for this API key. Load the model list again.')
            probe = candidate._generate('Return JSON only: {"ok": true}. This checks structured output for the bot.',structured=True)
            if probe.get('ok') is not True:
                raise ValueError('This model did not pass the bot compatibility test. Choose another model.')
            update_environment(settings_environment_path(BASE_DIR),{'GEMINI_API_KEY':key,'GEMINI_MODEL':model})
        except Exception:
            candidate.client.close()
            raise
        ai_service, GEMINI_KEY = candidate, key
    add_log('Gemini connection tested and saved.')
    return {'status':'ok','message':'API key tested and saved. It is ready to use; no restart needed.'}


@app.post('/api/settings/disconnect-youtube')
def disconnect_youtube():
    with configuration_change():
        yt_service.disconnect()
        oauth_flows.clear()
        if ai_service:
            ai_service.cached_growth_plan = None
    add_log('YouTube disconnected from this bot. Connect again to resume.')
    return {'status':'ok','message':'YouTube disconnected. Your channel videos are unchanged.'}


@app.get('/login',response_class=HTMLResponse)
def login_page(request:Request):
    return templates.TemplateResponse(request=request,name='login.html',context={'configured':bool(ADMIN_PASSWORD)})


@app.post('/login')
def do_login(request:Request,password:str=Form(...)):
    host = request.client.host if request.client else 'unknown'
    recent = [t for t in login_attempts.get(host,[]) if time.time()-t<300]
    login_attempts[host] = recent
    if len(recent)>=10:
        return JSONResponse({'error':'Too many attempts. Try again in five minutes.'},status_code=429)
    if not ADMIN_PASSWORD:
        return JSONResponse({'error':'Set ADMIN_PASSWORD in .env and restart.'},status_code=503)
    if hmac.compare_digest(password.encode(),ADMIN_PASSWORD.encode()):
        login_attempts.pop(host,None)
        response = RedirectResponse('/',status_code=303)
        response.set_cookie('auth_session',signer.dumps('admin',salt='login'),httponly=True,samesite='lax',secure=request.url.scheme=='https',max_age=43200)
        return response
    recent.append(time.time())
    return RedirectResponse('/login?error=1',status_code=303)


@app.post('/logout')
def logout():
    response = RedirectResponse('/login',status_code=303)
    response.delete_cookie('auth_session')
    return response


def oauth_config():
    client_id,client_secret = os.getenv('YOUTUBE_CLIENT_ID'),os.getenv('YOUTUBE_CLIENT_SECRET')
    if client_id and client_secret:
        return {'web':{'client_id':client_id,'client_secret':client_secret,'auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}
    file = BASE_DIR/'client_secrets.json'
    if file.exists():
        return json.loads(file.read_text(encoding='utf-8-sig'))
    raise ValueError('Add YouTube OAuth client credentials to .env or client_secrets.json.')


@app.get('/auth/youtube')
def connect_youtube(request:Request):
    base = os.getenv('PUBLIC_BASE_URL','http://localhost:8000').rstrip('/')
    if urlparse(base).scheme != 'https' and urlparse(base).hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('PUBLIC_BASE_URL must use HTTPS outside localhost.')
    flow = Flow.from_client_config(oauth_config(),scopes=SCOPES,redirect_uri=base+'/auth/callback',autogenerate_code_verifier=True)
    auth_url,state = flow.authorization_url(prompt='consent',access_type='offline')
    for key,value in list(oauth_flows.items()):
        if time.time()-value[1]>600:
            oauth_flows.pop(key,None)
    oauth_flows[state] = (flow,time.time())
    response = RedirectResponse(auth_url)
    response.set_cookie('oauth_state',state,httponly=True,samesite='lax',secure=base.startswith('https://'),max_age=600)
    return response


@app.get('/auth/callback')
def auth_callback(request:Request,state:str='',code:str='',error:str=''):
    if yt_service.creation_lock.locked() or active_job['active']:
        raise ValueError('Wait for the current Short to finish before changing channels.')
    entry = oauth_flows.pop(state,None)
    if not entry or not state or not hmac.compare_digest(request.cookies.get('oauth_state',''),state) or time.time()-entry[1]>600:
        raise ValueError('OAuth state expired or invalid. Start Connect YouTube again.')
    if error or not code:
        raise ValueError('YouTube permission was not granted. Try connecting again.')
    flow = entry[0]
    flow.fetch_token(code=code)
    with configuration_change():
        yt_service.connect(flow.credentials)
        yt_service.save_credentials(flow.credentials)
    if ai_service:
        ai_service.cached_growth_plan = None
    response = RedirectResponse('/#settings',status_code=303)
    response.delete_cookie('oauth_state')
    return response


@app.get('/',response_class=HTMLResponse)
def index(request:Request):
    analytics = yt_service.get_detailed_analytics()
    categorized = yt_service.get_categorized_channel_videos()
    combined = [v for items in categorized.values() for v in items]
    growth = ai_service.cached_growth_plan if ai_service and ai_service.cached_growth_plan else dict(detected_niche='Not analyzed',health_score='Not measured',ideas=[])
    comments = yt_service.get_recent_comments_2days()
    return templates.TemplateResponse(request=request,name='index.html',context=dict(
        yt_connected=bool(yt_service.youtube and yt_service.channel_id),ai_configured=bool(ai_service),
        automation_running=scheduler.running and yt_service.settings.get('automation_enabled'),channel_id=yt_service.channel_id,channel_name=yt_service.channel_name,
        ai_model=ai_service.model if ai_service else '',saved_source_ids=yt_service.saved_source_ids(),
        source_videos=[v for category in ('videos','live') for v in categorized[category] if not v.get('is_live') and parse_iso_duration(v.get('duration'))>=30],
        analytics=analytics,categorized=categorized,total_uploads_count=len(combined),playlist_id=yt_service.cached_playlist_id or '',
        drafts=[d for d in yt_service.drafts if d.get('channel_id') in (None,yt_service.channel_id)],growth_plan=growth,
        settings=yt_service.settings,scheduled_slots=[s.strip() for s in yt_service.settings['posting_times'].split(',')][:yt_service.settings['daily_shorts_count']],
        live_streams=categorized['live'][:10],latest_stream_title=comments['active_stream_title'],comments_hub=comments,
        errors=list(yt_service.errors.values()),logs=list(logs_history),timezone=str(yt_service.tz)))


@app.get('/api/job-status')
def job_status():
    with job_lock:
        return dict(active_job)


@app.get('/api/live-chat-status')
def live_status():
    return {**yt_service.get_active_live_stream_info(),'messages':list(yt_service.live_chat_history)[-25:],
            'reply_enabled':bool(yt_service.settings.get('live_chat_reply') and ai_service and scheduler.running and yt_service.settings.get('automation_enabled'))}


@app.post('/api/send-live-chat')
def send_live_chat(message:str=Form(...)):
    yt_service.send_live_chat_message(message)
    return {'status':'ok'}


@app.post('/generate-growth-plan')
def growth_plan():
    if not ai_service or not yt_service.youtube:
        raise ValueError('Connect YouTube and configure Gemini first.')
    stats = yt_service.get_detailed_analytics()
    videos = sorted([v for items in yt_service.get_categorized_channel_videos().values() for v in items],key=lambda v:v['published_at'],reverse=True)
    ai_service.generate_detailed_growth_plan(yt_service.channel_name,dict(subscribers=stats['subscribers'],recent_videos=[{'title':v['title']} for v in videos[:10]]))
    return RedirectResponse('/',status_code=303)


def enqueue(background_tasks,fn,title,*args):
    if not yt_service.youtube or not ai_service:
        return JSONResponse({'error':'Connect YouTube and configure Gemini first.'},status_code=409)
    with job_lock:
        if active_job['active'] or yt_service.creation_lock.locked():
            return JSONResponse({'error':'A Short is already being created. Wait for it to finish.'},status_code=409)
        active_job.update(active=True,job_id=secrets.token_hex(12),video_title=title,status='starting',progress=0,step_index=1,
                          step_text='Preparing…',video_id='',short_url='',playlist_url='',error='')
        job_id = active_job['job_id']
    def report(*values, **keywords):
        with job_lock:
            if active_job['job_id'] == job_id:
                update_job_status(*values, **keywords)
    def execute():
        try:
            fn(*args,add_log,report)
        except Exception as exc:
            error = yt_service.report_error('Creation job',exc)
            report(0,0,'error',error,error=error)
        finally:
            with job_lock:
                if active_job['job_id'] == job_id and active_job['active']:
                    report(0,0,'error','Job stopped before completion.',error='Job stopped before completion.')
    background_tasks.add_task(execute)
    return JSONResponse({'status':'started','job_id':job_id,'video_title':title},status_code=202)


@app.post('/create-custom-short')
def custom_short(background_tasks:BackgroundTasks,video_id:str=Form(...),video_title:str=Form(''),action:str=Form('instant'),custom_time:str=Form('18:00')):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
        raise ValueError('Invalid YouTube video ID.')
    if action == 'schedule':
        action = 'scheduled'
    if action not in ('instant','scheduled'):
        raise ValueError('Invalid action.')
    if action=='scheduled':
        yt_service.next_slot(custom_time)
    return enqueue(background_tasks,yt_service.create_custom_short_from_video,video_title,video_id,video_title,ai_service,action,custom_time)


@app.post('/create-instant-short-latest')
def latest_short(background_tasks:BackgroundTasks):
    target = yt_service.select_source_video_for_shorts()
    if not target:
        return JSONResponse({'error':'No completed source video is available.'},status_code=409)
    return enqueue(background_tasks,yt_service.create_custom_short_from_video,target['title'],target['id'],target['title'],ai_service,'instant','')


@app.post('/prestage-draft-now')
def prestage(background_tasks:BackgroundTasks):
    slots = [s.strip() for s in yt_service.settings['posting_times'].split(',')][:yt_service.settings['daily_shorts_count']]
    target = min(yt_service.next_slot(s) for s in slots)
    slot = target.strftime('%H:%M')
    key = f'{target:%Y-%m-%d}_{slot}'
    if key in yt_service.history or any(d.get('slot')==key for d in yt_service.drafts):
        return JSONResponse({'error':'This slot already has a Short.'},status_code=409)
    return enqueue(background_tasks,yt_service.create_prestage_draft,'Scheduled draft',ai_service,slot,key)


@app.post('/reply-comment-single')
def reply_comment(comment_id:str=Form(...),author:str=Form('Viewer'),comment_text:str=Form(''),custom_reply:str=Form('')):
    yt_service.reply_to_single_comment(comment_id,author,comment_text,custom_reply,ai_service,add_log)
    return RedirectResponse('/#commentsHubSection',status_code=303)


@app.post('/reply-all-comments')
def reply_all(background_tasks:BackgroundTasks):
    if not ai_service or not yt_service.youtube:
        raise ValueError('Connect YouTube and configure Gemini first.')
    background_tasks.add_task(run_safely,'Reply all',yt_service.reply_all_pending_comments,ai_service,add_log)
    add_log('Comment replies queued. Confirmed results appear in Activity.')
    return RedirectResponse('/#commentsHubSection',status_code=303)


@app.post('/boost-video-seo')
def boost_video(video_id:str=Form(...)):
    yt_service.boost_video_seo(video_id,ai_service,add_log)
    return RedirectResponse('/',status_code=303)


@app.post('/boost-all-seo')
def boost_all(background_tasks:BackgroundTasks):
    if not ai_service or not yt_service.youtube:
        raise ValueError('Connect YouTube and configure Gemini first.')
    background_tasks.add_task(run_safely,'SEO update',yt_service.auto_scan_and_boost_channel_seo,ai_service,add_log)
    add_log('Metadata updates queued. Confirmed results appear in Activity.')
    return RedirectResponse('/',status_code=303)


@app.post('/publish-draft-now')
def publish(video_id:str=Form(...)):
    yt_service.publish_draft_instantly(video_id,add_log)
    return RedirectResponse('/',status_code=303)


@app.post('/update-settings')
async def settings(request:Request,comments_reply:bool=Form(False),live_chat_reply:bool=Form(False),privacy_guard:bool=Form(False),
                   auto_stream_select:bool=Form(False),daily_shorts_count:int=Form(3),post_mode:str=Form('scheduled'),automation_enabled:bool|None=Form(None)):
    form = await request.form()
    cfg = dict(comments_reply=comments_reply,live_chat_reply=live_chat_reply,privacy_guard=privacy_guard,
        auto_stream_select=auto_stream_select,daily_shorts_count=daily_shorts_count,post_mode=post_mode,
        posting_times=', '.join(str(t).strip() for t in form.getlist('time_slot')))
    if automation_enabled is not None:
        cfg['automation_enabled'] = automation_enabled
    yt_service.save_settings(cfg)
    add_log('Settings saved. Posting times use '+str(yt_service.tz))
    if 'application/json' in request.headers.get('accept',''):
        return scheduler_status()
    return RedirectResponse('/#scheduler',status_code=303)


@app.get('/api/scheduler-status')
def scheduler_status():
    settings = dict(yt_service.settings)
    running = bool(scheduler.running and settings.get('automation_enabled'))
    blockers = []
    if not running: blockers.append('Automation paused. Choose Running and Save & Apply.')
    if not settings.get('auto_stream_select'): blockers.append('Automatic Shorts Creation is OFF.')
    if not yt_service.youtube: blockers.append('Connect YouTube in Settings.')
    if not ai_service: blockers.append('Save a Gemini API key in Settings.')
    slots = [s.strip() for s in settings['posting_times'].split(',')][:settings['daily_shorts_count']]
    now = dt.datetime.now(yt_service.tz)
    upcoming = min(yt_service.next_slot(slot,now) for slot in slots)
    retries = [v for k,v in yt_service.slot_retries.items() if k.startswith(f'{yt_service.channel_id}:')]
    last = max(retries,key=lambda v:v.get('failed_at',''),default={})
    retry_at = last.get('next_retry',0)
    return dict(settings=settings,running=running,timezone=str(yt_service.tz),server_time=now.isoformat(),
                next_slot=upcoming.isoformat(),blockers=blockers,last_failure=last.get('last_error',''),
                retry_at=dt.datetime.fromtimestamp(retry_at,yt_service.tz).isoformat() if retry_at else '',
                saved_recordings=len(yt_service.saved_source_ids()),job=job_status())


@app.post('/api/settings/timing')
async def save_timing(request:Request,daily_shorts_count:int=Form(...),post_mode:str=Form(...)):
    form = await request.form()
    yt_service.save_timing(daily_shorts_count,[str(s).strip() for s in form.getlist('time_slot')],post_mode)
    add_log('Posting times saved: '+yt_service.settings['posting_times']+' ('+str(yt_service.tz)+').')
    return scheduler_status()
