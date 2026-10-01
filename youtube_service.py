import datetime as dt
import functools
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo
import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from runtime_config import data_directory

SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl', 'https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
UTC = dt.timezone.utc


def parse_iso_duration(value):
    match = re.fullmatch(r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', value or '')
    return int(sum(float(n or 0)*f for n, f in zip(match.groups(), (86400,3600,60,1)))) if match else 0


from media_tools import get_ffmpeg_path, probe_video, download_clip_robustly, convert_vertical_clip, trim_local


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False, suffix='.tmp') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        temp = f.name
    os.replace(temp, path)


def serialized(fn):
    @functools.wraps(fn)
    def call(self, *args, **kwargs):
        with self.data_lock:
            return fn(self, *args, **kwargs)
    return call


class YouTubeService:
    def __init__(self, base_dir=None, load_credentials=False):
        self.base_dir = str(Path(base_dir).resolve() if base_dir else data_directory())
        Path(self.base_dir).mkdir(parents=True,exist_ok=True)
        self.tz = ZoneInfo(os.getenv('BOT_TIMEZONE', 'Asia/Kolkata'))
        self.api_lock, self.data_lock, self.creation_lock = threading.RLock(), threading.RLock(), threading.Lock()
        self.youtube, self.channel_id = None, None
        self.channel_name, self.last_error, self.errors = 'Not connected', '', {}
        self.cache_channel_id, self.cached_playlist_id = None, None
        self.cached_videos_data = {'live': [], 'videos': [], 'shorts': []}
        self.cached_videos_time, self.cached_analytics_time = 0, 0
        self.cached_analytics, self._cached_live_info, self._cached_live_time = None, None, 0
        self.comments_time, self.comments_data, self.live_chat_history = 0, [], []
        self.chat_id, self.chat_page, self.next_chat_poll = None, None, 0
        self.pending_live, self.processed_live_messages = [], set()
        self.live_reply_receipts = self._load('live_reply_receipts.json', {})
        self.history = self._load('clips_history.json', {})
        self.drafts = self._load('draft_queue.json', [])
        self.replied_comments_map = self._load('replied_comments.json', {})
        defaults = dict(comments_reply=False, live_chat_reply=False, privacy_guard=False, auto_stream_select=False,
                        daily_shorts_count=3, post_mode='scheduled', posting_times='06:00, 11:00, 18:30')
        self.settings = {**defaults, **self._load('bot_settings.json', {})}
        cache = self._load('cached_channel_videos.json', {})
        if cache.get('channel_id') and isinstance(cache.get('data'), dict):
            self.cache_channel_id, self.cached_videos_data = cache['channel_id'], cache['data']
            self.cached_videos_time = cache.get('fetched_at', 0)
        if load_credentials:
            self.load_saved_credentials()

    def _load(self, name, default):
        try:
            value = json.loads((Path(self.base_dir)/name).read_text(encoding='utf-8-sig'))
            return value if isinstance(value, type(default)) else default
        except (OSError, ValueError):
            return default

    def _save(self, name, value):
        atomic_json(Path(self.base_dir)/name, value)

    def report_error(self, area, exc):
        status, reason = getattr(getattr(exc, 'resp', None), 'status', None), ''
        if getattr(exc, 'content', None):
            try:
                errors = json.loads(exc.content).get('error', {}).get('errors', [])
                reason = errors[0].get('reason', '') if errors else ''
            except (ValueError, TypeError):
                pass
        detail = f'HTTP {status} {reason}' if status else str(exc)
        if reason == 'uploadLimitExceeded':
            detail = 'YouTube channel upload limit reached (uploadLimitExceeded). No new video was accepted. Wait for the channel limit to reset; check YouTube Studio feature eligibility before retrying.'
        detail = re.sub(r'AIza[\w-]+', '[redacted]', re.sub(r'https?://\S+', '[URL]', detail))
        self.last_error = f'{area}: {detail[:400]}'
        self.errors[area] = self.last_error
        return self.last_error

    def _execute(self, request):
        with self.api_lock:
            return request.execute()

    @serialized
    def connect(self, credentials):
        with self.api_lock:
            transport = httplib2.Http(timeout=60)
            # HTTP 308 is the resumable upload acknowledgement, not a redirect.
            transport.redirect_codes = transport.redirect_codes - {308}
            client = build('youtube', 'v3', http=AuthorizedHttp(credentials, http=transport), cache_discovery=False)
            result = client.channels().list(part='id,snippet', mine=True).execute()
            if not result.get('items'):
                raise RuntimeError('Selected Google account has no YouTube channel. Reconnect with the correct channel.')
            channel = result['items'][0]
            self.youtube, self.channel_id, self.channel_name = client, channel['id'], channel['snippet']['title']
            if self.cache_channel_id != self.channel_id:
                self.cached_videos_data, self.cached_videos_time = {'live': [], 'videos': [], 'shorts': []}, 0
            self.cache_channel_id = self.channel_id
            self.cached_analytics_time, self._cached_live_time, self.comments_time = 0, 0, 0
            self.cached_analytics, self._cached_live_info, self.comments_data = None, None, []
            self.live_chat_history, self.cached_playlist_id = [], None
            self.chat_id, self.chat_page, self.next_chat_poll = None, None, 0
            self.pending_live = []
            self.errors.pop('YouTube connection', None)
            self.last_error = ''

    def load_saved_credentials(self):
        if self._load('youtube_connection.json', {}).get('disconnected'):
            return
        try:
            token, legacy = Path(self.base_dir)/'token.json', Path(self.base_dir)/'token.pickle'
            if token.exists():
                credentials = Credentials.from_authorized_user_file(str(token))
            elif legacy.exists():
                with legacy.open('rb') as f:
                    credentials = pickle.load(f)
            else:
                return
            if not credentials.valid:
                if not credentials.refresh_token:
                    raise RuntimeError('YouTube login expired. Reconnect your channel.')
                credentials.refresh(Request())
            self.connect(credentials)
            self.save_credentials(credentials)
        except Exception as exc:
            self.youtube = None
            self.report_error('YouTube connection', exc)

    def save_credentials(self, credentials):
        self._save('token.json', json.loads(credentials.to_json()))
        self._save('youtube_connection.json', {'disconnected': False})

    @serialized
    def disconnect(self):
        with self.api_lock:
            self._save('youtube_connection.json', {'disconnected': True})
            self.youtube, self.channel_id, self.channel_name = None, None, 'Not connected'
            self.cached_videos_data = {'live': [], 'videos': [], 'shorts': []}
            self.cached_videos_time = self.cached_analytics_time = self._cached_live_time = self.comments_time = 0
            self.cached_analytics = self._cached_live_info = self.cached_playlist_id = None
            self.comments_data, self.live_chat_history, self.pending_live = [], [], []
            self.chat_id, self.chat_page = None, None
            self.errors.clear()
            for name in ('token.json', 'token.pickle'):
                (Path(self.base_dir)/name).unlink(missing_ok=True)

    @serialized
    def save_settings(self, cfg):
        count = cfg.get('daily_shorts_count')
        slots = [s.strip() for s in cfg.get('posting_times', '').split(',') if s.strip()]
        if not isinstance(count, int) or not 1 <= count <= 5:
            raise ValueError('Choose between 1 and 5 Shorts per day.')
        if len(slots) != count or len(set(slots)) != count:
            raise ValueError('Choose one unique time for each daily Short.')
        if any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', s) for s in slots):
            raise ValueError('Invalid posting time; use HH:MM.')
        if cfg.get('post_mode') not in ('scheduled', 'immediate'):
            raise ValueError('Invalid posting mode.')
        self._save('bot_settings.json', cfg)
        self.settings = cfg

    def get_detailed_analytics(self):
        if self.cached_analytics and time.time()-self.cached_analytics_time < 300:
            return dict(self.cached_analytics, source='YouTube API (cached up to 5 minutes)')
        out = dict(name=self.channel_name, subscribers='Unavailable', views='Unavailable', videos='Unavailable', joined='Unavailable',
                   source='Unavailable', error='', live_count=0, vod_count=0, shorts_count=0)
        if not self.youtube:
            out['error'] = self.last_error or 'Connect YouTube to load channel data.'
            return out
        try:
            result = self._execute(self.youtube.channels().list(part='snippet,statistics', mine=True))
            if not result.get('items'):
                raise RuntimeError('No channel returned by YouTube.')
            item = result['items'][0]
            stats = item.get('statistics', {})
            for key, api_key in [('subscribers','subscriberCount'), ('views','viewCount'), ('videos','videoCount')]:
                if api_key in stats:
                    out[key] = f'{int(stats[api_key]):,}'
            if stats.get('hiddenSubscriberCount'):
                out['subscribers'] = 'Hidden by channel'
            out.update(name=item['snippet']['title'], joined=item['snippet'].get('publishedAt','')[:10] or 'Unavailable',
                       source='YouTube Data API', fetched_at=dt.datetime.now(UTC).isoformat())
            self.cached_analytics, self.cached_analytics_time = out.copy(), time.time()
            self.errors.pop('Channel statistics', None)
        except Exception as exc:
            out['error'] = self.report_error('Channel statistics', exc)
        return out

    def get_categorized_channel_videos(self):
        if not self.youtube:
            return {'live': [], 'videos': [], 'shorts': []}
        if time.time()-self.cached_videos_time < 900:
            return self.cached_videos_data
        try:
            channels = self._execute(self.youtube.channels().list(part='contentDetails', mine=True))
            uploads = channels['items'][0]['contentDetails']['relatedPlaylists']['uploads']
            ids, page = [], None
            while True:
                result = self._execute(self.youtube.playlistItems().list(part='contentDetails', playlistId=uploads, maxResults=50, pageToken=page))
                ids.extend(i['contentDetails']['videoId'] for i in result.get('items', []))
                page = result.get('nextPageToken')
                if not page:
                    break
            cats = {'live': [], 'videos': [], 'shorts': []}
            for start in range(0,len(ids),50):
                result = self._execute(self.youtube.videos().list(part='snippet,statistics,status,contentDetails,liveStreamingDetails,fileDetails', id=','.join(ids[start:start+50])))
                for item in result.get('items', []):
                    snip, stats = item['snippet'], item.get('statistics', {})
                    duration = item.get('contentDetails', {}).get('duration', '')
                    streams = item.get('fileDetails', {}).get('videoStreams', [])
                    vertical = bool(streams and streams[0].get('heightPixels',0) >= streams[0].get('widthPixels',1))
                    short = 0 < parse_iso_duration(duration) <= 180 and (vertical or '#shorts' in (snip['title']+snip.get('description','')).lower())
                    cat = 'live' if item.get('liveStreamingDetails') else 'shorts' if short else 'videos'
                    cats[cat].append(dict(id=item['id'], title=snip['title'], description=snip.get('description',''), duration=duration,
                        views=f"{int(stats['viewCount']):,}" if 'viewCount' in stats else 'Unavailable',
                        likes=f"{int(stats['likeCount']):,}" if 'likeCount' in stats else 'Unavailable',
                        published_at=snip.get('publishedAt',''), thumbnail=snip.get('thumbnails',{}).get('medium',{}).get('url',''),
                        privacy=item.get('status',{}).get('privacyStatus','unknown').upper(), is_live=snip.get('liveBroadcastContent')=='live'))
            self.cached_videos_data, self.cached_videos_time = cats, time.time()
            self._save('cached_channel_videos.json',dict(channel_id=self.channel_id, fetched_at=self.cached_videos_time,data=cats))
            self.errors.pop('Video library',None)
        except Exception as exc:
            self.report_error('Video library',exc)
        return self.cached_videos_data

    def get_active_live_stream_info(self):
        if self._cached_live_info and time.time()-self._cached_live_time < 60:
            return self._cached_live_info
        out = dict(is_live=False,title='No active live stream',viewers=None,likes=None,chat_id=None)
        if self.youtube:
            try:
                result = self._execute(self.youtube.liveBroadcasts().list(part='snippet,status',broadcastStatus='active',maxResults=1))
                if result.get('items'):
                    item = result['items'][0]
                    out.update(is_live=True,id=item['id'],title=item['snippet']['title'],description=item['snippet'].get('description',''),
                               chat_id=item['snippet'].get('liveChatId'),privacy=item.get('status',{}).get('privacyStatus'))
                    metrics = self._execute(self.youtube.videos().list(part='statistics,liveStreamingDetails',id=item['id']))
                    if metrics.get('items'):
                        video = metrics['items'][0]
                        out['viewers'] = video.get('liveStreamingDetails',{}).get('concurrentViewers')
                        out['likes'] = video.get('statistics',{}).get('likeCount')
                self.errors.pop('Live status',None)
            except Exception as exc:
                out['error'] = self.report_error('Live status',exc)
        self._cached_live_info,self._cached_live_time = out,time.time()
        return out

    @serialized
    def get_recent_comments_2days(self):
        live = self.get_active_live_stream_info()
        if self.youtube and time.time()-self.comments_time >= 120:
            try:
                comments,page,done = [],None,False
                cutoff = dt.datetime.now(UTC)-dt.timedelta(hours=24)
                library = {v['id']:(cat,v) for cat,items in self.cached_videos_data.items() for v in items}
                while not done:
                    result = self._execute(self.youtube.commentThreads().list(part='snippet,replies',allThreadsRelatedToChannelId=self.channel_id,
                        maxResults=100,order='time',textFormat='plainText',pageToken=page))
                    for item in result.get('items',[]):
                        top_comment = item['snippet']['topLevelComment']
                        top,cid = top_comment['snippet'],top_comment['id']
                        if dt.datetime.fromisoformat(top['publishedAt'].replace('Z','+00:00')) < cutoff:
                            done = True
                            break
                        if top.get('authorChannelId',{}).get('value') == self.channel_id:
                            continue
                        reply = self.replied_comments_map.get(cid,'')
                        for r in item.get('replies',{}).get('comments',[]):
                            if r['snippet'].get('authorChannelId',{}).get('value') == self.channel_id:
                                reply = r['snippet'].get('textOriginal',r['snippet'].get('textDisplay',''))
                        cat,video = library.get(top.get('videoId'),('videos',{}))
                        if cat == 'live' and not video.get('is_live'):
                            cat = 'live_finished'
                        comments.append(dict(id=cid,author=top.get('authorDisplayName','Viewer'),text=top.get('textOriginal',top.get('textDisplay','')),
                            published_at=top['publishedAt'],video_id=top.get('videoId',''),video_title=video.get('title','Channel upload'),
                            video_thumbnail=video.get('thumbnail',''),category=cat,likes=top.get('likeCount',0),
                            is_replied=bool(reply),bot_reply=reply,bot_reply_time='Confirmed reply' if reply else ''))
                    page = result.get('nextPageToken')
                    if not page:
                        break
                self.comments_data,self.comments_time = comments,time.time()
                self.errors.pop('Comments',None)
            except Exception as exc:
                self.report_error('Comments',exc)
                self.comments_time = time.time()
        comments = self.comments_data if self.youtube else []
        replied = sum(c['is_replied'] for c in comments)
        return dict(all=comments,comments=comments,stats=dict(total=len(comments),replied=replied,pending=len(comments)-replied),
            **{cat:[c for c in comments if c['category']==cat] for cat in ('videos','shorts','live','live_finished')},
            live_chat_active=live['is_live'],active_stream_title=live['title'],live_viewers=live['viewers'],live_likes=live['likes'],
            live_chat_history=self.live_chat_history,error=self.errors.get('Comments',''))

    @serialized
    def reply_to_single_comment(self,comment_id,author,comment_text,custom_reply,ai_service,log_fn=print):
        if not self.youtube:
            raise RuntimeError('Connect YouTube first.')
        if comment_id in self.replied_comments_map:
            raise RuntimeError('This comment already has a confirmed bot reply.')
        original = self._execute(self.youtube.comments().list(part='snippet',id=comment_id)).get('items',[])
        if not original or original[0]['snippet'].get('channelId') != self.channel_id:
            raise ValueError('Comment does not belong to the connected channel.')
        source = original[0]['snippet']
        page = None
        while True:
            replies = self._execute(self.youtube.comments().list(part='snippet', parentId=comment_id, maxResults=100, pageToken=page))
            for existing in replies.get('items', []):
                if existing['snippet'].get('authorChannelId', {}).get('value') == self.channel_id:
                    self.replied_comments_map[comment_id] = existing['snippet'].get('textOriginal', 'Confirmed channel reply')
                    self._save('replied_comments.json', self.replied_comments_map)
                    self.comments_time = 0
                    log_fn('Skipped: this comment already has a channel reply.')
                    return False
            page = replies.get('nextPageToken')
            if not page:
                break
        if not custom_reply and not ai_service:
            raise RuntimeError('Configure Gemini before generating replies.')
        reply = custom_reply.strip() if custom_reply else ai_service.generate_comment_reply(source.get('textOriginal',''),source.get('authorDisplayName','Viewer'))
        result = self._execute(self.youtube.comments().insert(part='snippet',body={'snippet':{'parentId':comment_id,'textOriginal':reply}}))
        if not result.get('id'):
            raise RuntimeError('YouTube did not confirm the reply.')
        self.replied_comments_map[comment_id] = reply
        self._save('replied_comments.json',self.replied_comments_map)
        self.comments_time = 0
        log_fn('Comment reply confirmed by YouTube.')
        return True

    def reply_all_pending_comments(self,ai_service,log_fn=print):
        for comment in self.get_recent_comments_2days()['comments']:
            if not comment['is_replied']:
                try:
                    self.reply_to_single_comment(comment['id'],comment['author'],comment['text'],'',ai_service,log_fn)
                except Exception as exc:
                    log_fn(self.report_error('Reply',exc))
                    break

    def check_and_quick_reply_comments(self,ai_service,log_fn=print):
        if self.settings.get('comments_reply') and ai_service:
            self.reply_all_pending_comments(ai_service,log_fn)

    @serialized
    def check_and_reply_live(self,ai_service,log_fn=print):
        if not self.youtube or not self.settings.get('live_chat_reply') or not ai_service or time.time()<self.next_chat_poll:
            return
        live = self.get_active_live_stream_info()
        chat = live.get('chat_id')
        if not chat:
            return
        if chat != self.chat_id:
            self.chat_id,self.chat_page = chat,None
            self.pending_live,self.live_chat_history = [],[]
            receipt_key = f'{self.channel_id}:{chat}'
            self.processed_live_messages = set(self.live_reply_receipts.get(receipt_key, []))
        try:
            if not self.pending_live:
                result = self._execute(self.youtube.liveChatMessages().list(part='snippet,authorDetails',liveChatId=chat,maxResults=200,pageToken=self.chat_page))
                self.next_chat_poll = time.time()+max(6,result.get('pollingIntervalMillis',6000)/1000)
                self.chat_page = result.get('nextPageToken')
                self.pending_live.extend(result.get('items',[]))
            for _ in range(min(3,len(self.pending_live))):
                message = self.pending_live[0]
                mid,author,snip = message['id'],message.get('authorDetails',{}),message.get('snippet',{})
                if mid in self.processed_live_messages or author.get('isChatOwner') or author.get('channelId')==self.channel_id or snip.get('type')!='textMessageEvent':
                    self.pending_live.pop(0)
                    continue
                text = snip.get('displayMessage','')
                reply = ai_service.generate_live_reply(text,author.get('displayName','Viewer'),live['title'],live.get('description',''))
                self._send_chat(chat,reply)
                self.live_chat_history.append(dict(author=author.get('displayName','Viewer'),message=text,reply=reply,time=dt.datetime.now(self.tz).strftime('%H:%M:%S')))
                self.live_chat_history = self.live_chat_history[-30:]
                self.processed_live_messages.add(mid)
                self.pending_live.pop(0)
                receipt_key = f'{self.channel_id}:{chat}'
                receipts = self.live_reply_receipts.setdefault(receipt_key, [])
                receipts.append(mid)
                self.live_reply_receipts[receipt_key] = receipts[-5000:]
                self._save('live_reply_receipts.json', self.live_reply_receipts)
            self.errors.pop('Live reply',None)
        except Exception as exc:
            self.next_chat_poll = time.time()+60
            log_fn(self.report_error('Live reply',exc))

    def _send_chat(self,chat,message):
        result = self._execute(self.youtube.liveChatMessages().insert(part='snippet',body={'snippet':{'liveChatId':chat,'type':'textMessageEvent','textMessageDetails':{'messageText':message}}}))
        if not result.get('id'):
            raise RuntimeError('YouTube did not confirm message delivery.')

    @serialized
    def send_live_chat_message(self,message):
        if not message.strip() or len(message)>200:
            raise ValueError('Live chat messages must contain 1–200 characters.')
        live = self.get_active_live_stream_info()
        if not live.get('chat_id'):
            raise RuntimeError('No active live chat is available.')
        self._send_chat(live['chat_id'],message)
        self.live_chat_history.append(dict(author=self.channel_name,message=message,reply='Confirmed sent',time=dt.datetime.now(self.tz).strftime('%H:%M:%S')))
        self.live_chat_history = self.live_chat_history[-30:]
        return True

    def select_source_video_for_shorts(self):
        cats = self.get_categorized_channel_videos()
        pool = [v for cat in ('live','videos') for v in cats[cat] if not v.get('is_live') and parse_iso_duration(v.get('duration'))>=30]
        saved = set(self.saved_source_ids())
        return max(pool,key=lambda v:(v['id'] in saved,v.get('published_at','')),default={})

    def next_slot(self,slot,now=None):
        now = now or dt.datetime.now(self.tz)
        hour,minute = map(int,slot.split(':'))
        target = now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        return target if target>now else target+dt.timedelta(days=1)

    def get_or_create_public_playlist(self,log_fn=print):
        if self.cached_playlist_id:
            return self.cached_playlist_id
        title,page = '⚡ Channel Shorts & Highlights',None
        while True:
            result = self._execute(self.youtube.playlists().list(part='snippet,status',mine=True,maxResults=50,pageToken=page))
            for item in result.get('items',[]):
                if item['snippet']['title']==title and item.get('status',{}).get('privacyStatus')=='public':
                    self.cached_playlist_id = item['id']
                    return item['id']
            page = result.get('nextPageToken')
            if not page:
                break
        result = self._execute(self.youtube.playlists().insert(part='snippet,status',body={'snippet':{'title':title},'status':{'privacyStatus':'public'}}))
        self.cached_playlist_id = result['id']
        return result['id']

    def add_video_to_playlist(self,playlist_id,video_id,log_fn=print):
        self._execute(self.youtube.playlistItems().insert(part='snippet',body={'snippet':{'playlistId':playlist_id,'resourceId':{'kind':'youtube#video','videoId':video_id}}}))
        return True

    def upload_video(self,file_path,title,description,tags,schedule_time=None,privacy='public',source=None):
        status = {'privacyStatus':'private' if schedule_time else privacy}
        if schedule_time:
            status['publishAt'] = schedule_time
        if source and 'selfDeclaredMadeForKids' in source.get('status',{}):
            status['selfDeclaredMadeForKids'] = source['status']['selfDeclaredMadeForKids']
        body = {'snippet':{'title':title[:100],'description':description[:5000],'tags':tags,
                          'categoryId':(source or {}).get('snippet',{}).get('categoryId','22')},'status':status}
        media = MediaFileUpload(file_path,mimetype='video/mp4',chunksize=8*1024*1024,resumable=True)
        request = self.youtube.videos().insert(part='snippet,status',body=body,media_body=media)
        response = None
        try:
            with self.api_lock:
                while response is None:
                    _,response = request.next_chunk(num_retries=2)
        finally:
            media.stream().close()
        if not response.get('id'):
            raise RuntimeError('Upload returned no video ID. Check YouTube Studio before retrying.')
        return response['id']

    @staticmethod
    def public_status(item):
        # Preserve the owner's audience and other writable status settings.
        status = {k:v for k,v in item.get('status',{}).items() if k in (
            'embeddable','license','publicStatsViewable','selfDeclaredMadeForKids','containsSyntheticMedia')}
        return dict(status,privacyStatus='public')

    def _source(self,video_id,allow_active=False):
        result = self._execute(self.youtube.videos().list(part='snippet,status,contentDetails,liveStreamingDetails',id=video_id))
        if not result.get('items') or result['items'][0]['snippet'].get('channelId')!=self.channel_id:
            raise ValueError('Select a video owned by the connected channel.')
        item = result['items'][0]
        if not allow_active and item['snippet'].get('liveBroadcastContent') in ('live','upcoming'):
            raise ValueError('Select a completed recording. Active stream clipping is not supported.')
        return item

    def source_recording_path(self, video_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) or not re.fullmatch(r'[A-Za-z0-9_-]+', self.channel_id or ''):
            raise ValueError('Connect YouTube and select a valid source video.')
        return Path(self.base_dir)/'source_recordings'/self.channel_id/(video_id+'.mp4')

    def saved_source_ids(self):
        if not self.channel_id:
            return []
        directory = self.source_recording_path('abcdefghijk').parent
        return sorted(p.stem for p in directory.glob('*.mp4') if re.fullmatch(r'[A-Za-z0-9_-]{11}', p.stem))

    def create_custom_short_from_video(self,video_id,video_title,ai_service,action,custom_time,log_fn=print,progress_fn=None,draft_key=None,target_time=None):
        report = progress_fn or (lambda *args,**kwargs:None)
        if not self.creation_lock.acquire(blocking=False):
            report(0,0,'error','Another Short is being created.',error='Another Short is being created.')
            return None
        uploaded_id = None
        try:
            if not self.youtube or not ai_service:
                raise RuntimeError('Connect YouTube and configure Gemini before creating a Short.')
            if action not in ('instant','scheduled','draft'):
                raise ValueError('Invalid Short action.')
            # Resolve the intended day before downloading; do not silently move a slow job to tomorrow.
            scheduled = target_time or (self.next_slot(custom_time) if action in ('scheduled','draft') else None)
            source = self._source(video_id)
            seconds = parse_iso_duration(source.get('contentDetails',{}).get('duration',''))
            if seconds < 30:
                raise ValueError('Choose a completed video at least 30 seconds long.')
            length = 30
            offset = max(0,int(self.history.get(video_id,0)))
            if offset + length > seconds:
                offset = 0
            part = int(self.history.get(f'part_{video_id}',0))+1
            recording = self.source_recording_path(video_id)
            report(1,10,'snipping','Cutting the saved recording…' if recording.is_file() else 'Downloading the selected source segment…')
            with tempfile.TemporaryDirectory(prefix='youtube-short-') as folder:
                raw,out = str(Path(folder)/'source.mp4'),str(Path(folder)/'short.mp4')
                ffmpeg = get_ffmpeg_path()
                if recording.is_file():
                    trim_local(recording,raw,offset,length,ffmpeg)
                else:
                    download_clip_robustly(f'https://www.youtube.com/watch?v={video_id}',offset,length,raw,ffmpeg,str(Path(ffmpeg).parent),log_fn)
                report(2,45,'converting','Converting and validating the vertical clip…')
                convert_vertical_clip(raw,out,length,log_fn)
                report(3,70,'generating_ai','Generating metadata from the source title and description…')
                metadata = ai_service.generate_deep_seo_metadata(source['snippet']['title'],offset//60,part,self.channel_name,source['snippet'].get('description',''))
                if scheduled and scheduled <= dt.datetime.now(self.tz):
                    raise RuntimeError('Scheduled time passed during creation. Choose a later slot.')
                publish_at = scheduled.astimezone(UTC).isoformat().replace('+00:00','Z') if scheduled else None
                report(4,85,'uploading','Uploading to YouTube…')
                vid = self.upload_video(out,metadata['title'],metadata['description'],metadata['tags'],publish_at,'private' if action=='draft' else 'public',source)
                uploaded_id = vid
                self.history[video_id] = offset+length if offset+2*length <= seconds else 0
                self.history[f'part_{video_id}'] = part
                if draft_key:
                    self.history[draft_key] = vid
                self._save('clips_history.json',self.history)
                if scheduled:
                    self.drafts.insert(0,dict(slot=draft_key or publish_at,slot_time=scheduled.strftime('%H:%M'),publish_at=publish_at,
                        channel_id=self.channel_id,youtube_id=vid,title=metadata['title'],description=metadata['description'],created_at=dt.datetime.now(self.tz).strftime('%H:%M'),status='Schedule requested; visibility checked after upload'))
                    self._save('draft_queue.json',self.drafts)
                warning = ''
                try:
                    playlist = self.get_or_create_public_playlist(log_fn)
                    self.add_video_to_playlist(playlist,vid,log_fn)
                except Exception as exc:
                    warning = ' Playlist addition failed; the upload exists.'
                    log_fn(self.report_error('Playlist',exc))
                self.cached_videos_time = 0
                actual = self._execute(self.youtube.videos().list(part='status',id=vid)).get('items',[])
                privacy = actual[0].get('status',{}).get('privacyStatus','unknown') if actual else 'unknown'
                scheduled_confirmed = bool(actual and actual[0].get('status',{}).get('publishAt'))
                schedule_note = (' Schedule confirmed.' if scheduled_confirmed else ' Schedule not confirmed; check YouTube Studio.') if scheduled else ''
                report(5,100,'completed',f'Upload confirmed. Visibility: {privacy}.'+schedule_note+warning,
                    video_id=vid,short_url=f'https://www.youtube.com/shorts/{vid}')
                return vid
        except Exception as exc:
            error = self.report_error('Short creation',exc)
            if uploaded_id:
                error += f' Upload already exists: {uploaded_id}. Check Studio before retrying.'
            log_fn(error)
            report(0,0,'error',error,error=error,video_id=uploaded_id or '')
            return None
        finally:
            self.creation_lock.release()

    def create_prestage_draft(self,ai_service,slot_time,draft_key,log_fn=print,progress_fn=None,target_time=None):
        source = self.select_source_video_for_shorts()
        if not source:
            if progress_fn:
                progress_fn(0,0,'error','No completed source video found.',error='No completed source video found.')
            return None
        return self.create_custom_short_from_video(source['id'],source['title'],ai_service,'draft',slot_time,log_fn,progress_fn,
            draft_key,target_time or self.next_slot(slot_time))

    def check_and_prestage_1h_drafts(self,ai_service,log_fn=print):
        if not self.youtube or not ai_service or not self.settings.get('auto_stream_select') or self.creation_lock.locked():
            return
        now = dt.datetime.now(self.tz)
        slots = [s.strip() for s in self.settings['posting_times'].split(',')][:self.settings['daily_shorts_count']]
        for slot in slots:
            for date in (now.date(),(now+dt.timedelta(days=1)).date()):
                target = dt.datetime.combine(date,dt.time.fromisoformat(slot),self.tz)
                delta,key = (target-now).total_seconds(),f'{date.isoformat()}_{slot}'
                if key in self.history or any(d.get('slot')==key for d in self.drafts):
                    continue
                if self.settings.get('post_mode')=='scheduled' and 0<delta<=3600:
                    self.create_prestage_draft(ai_service,slot,key,log_fn,target_time=target)
                    return
                if -600<=delta<=0:
                    source = self.select_source_video_for_shorts()
                    if source:
                        self.create_custom_short_from_video(source['id'],source['title'],ai_service,'instant','',log_fn,draft_key=key)
                    return

    @serialized
    def publish_draft_instantly(self,youtube_id,log_fn=print):
        draft = next((d for d in self.drafts if d.get('youtube_id')==youtube_id),None)
        if not draft:
            raise ValueError('Draft not found.')
        item = self._source(youtube_id)
        result = self._execute(self.youtube.videos().update(part='status',body={'id':youtube_id,'status':self.public_status(item)}))
        if result.get('status',{}).get('privacyStatus')!='public':
            raise RuntimeError('YouTube did not confirm public visibility. Draft retained.')
        self.drafts.remove(draft)
        self._save('draft_queue.json',self.drafts)
        log_fn('Draft publication confirmed by YouTube.')
        return True

    @serialized
    def auto_publish_due_drafts(self,log_fn=print):
        if not self.youtube:
            return
        changed = False
        for draft in list(self.drafts):
            if draft.get('channel_id') not in (None,self.channel_id) or not draft.get('publish_at'):
                continue
            if dt.datetime.fromisoformat(draft['publish_at'].replace('Z','+00:00'))>dt.datetime.now(UTC):
                continue
            result = self._execute(self.youtube.videos().list(part='status',id=draft['youtube_id']))
            if result.get('items') and result['items'][0].get('status',{}).get('privacyStatus')=='public':
                self.drafts.remove(draft)
                changed = True
        if changed:
            self._save('draft_queue.json',self.drafts)

    @serialized
    def check_and_update_stream_privacy(self,threshold_minutes=30,log_fn=print):
        """Publish non-public uploads and broadcasts when the user enables auto-public."""
        if not self.youtube or not self.settings.get('privacy_guard'):
            return
        # Refresh so videos changed outside this bot are included. Uploads playlist
        # covers Shorts, full videos and completed broadcasts, with pagination.
        self.cached_videos_time = 0
        cats = self.get_categorized_channel_videos()
        ids = {v['id'] for items in cats.values() for v in items if v.get('privacy') != 'PUBLIC'}
        for state in ('active', 'upcoming'):
            page = None
            while True:
                result = self._execute(self.youtube.liveBroadcasts().list(part='id,status',broadcastStatus=state,maxResults=50,pageToken=page))
                ids.update(v['id'] for v in result.get('items',[]) if v.get('status',{}).get('privacyStatus') != 'public')
                page = result.get('nextPageToken')
                if not page:
                    break
        for video_id in sorted(ids):
            try:
                item = self._source(video_id,allow_active=True)
                status = item.get('status',{})
                if status.get('privacyStatus') == 'public':
                    continue
                # User-selected future posting times remain in effect.
                if status.get('publishAt') and dt.datetime.fromisoformat(status['publishAt'].replace('Z','+00:00')) > dt.datetime.now(UTC):
                    continue
                if status.get('uploadStatus') in ('failed','rejected','deleted'):
                    continue
                result = self._execute(self.youtube.videos().update(part='status',body={'id':video_id,'status':self.public_status(item)}))
                if result.get('status',{}).get('privacyStatus') != 'public':
                    raise RuntimeError('YouTube did not confirm public visibility; check restrictions in Studio.')
                log_fn(f'Auto-public confirmed: {video_id}')
                self.errors.pop('Auto-public '+video_id,None)
            except Exception as exc:
                log_fn(self.report_error('Auto-public '+video_id,exc))
        self.cached_videos_time = self.cached_analytics_time = self._cached_live_time = 0

    def boost_video_seo(self,video_id,ai_service,log_fn=print):
        if not ai_service:
            raise RuntimeError('Configure Gemini first.')
        item = self._source(video_id,allow_active=True)
        snip = item['snippet']
        metadata = ai_service.generate_deep_seo_metadata(snip['title'],channel_name=self.channel_name,description=snip.get('description',''),is_short=False)
        editable = {k:v for k,v in snip.items() if k in ('title','description','tags','categoryId','defaultLanguage','defaultAudioLanguage')}
        editable.update(description=metadata['description'],tags=metadata['tags'])
        result = self._execute(self.youtube.videos().update(part='snippet',body={'id':video_id,'snippet':editable}))
        if not result.get('id'):
            raise RuntimeError('YouTube did not confirm metadata update.')
        self.cached_videos_time = 0
        log_fn('Metadata update confirmed by YouTube.')
        return True

    def auto_scan_and_boost_channel_seo(self,ai_service,log_fn=print):
        cats = self.get_categorized_channel_videos()
        for item in [v for items in cats.values() for v in items]:
            if item.get('is_live'):
                continue
            try:
                self.boost_video_seo(item['id'],ai_service,log_fn)
            except Exception as exc:
                log_fn(self.report_error('SEO update',exc))
                break
