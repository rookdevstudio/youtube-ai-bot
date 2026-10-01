import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ['BOT_OFFLINE'] = '1'
os.environ['BOT_AUTOMATION_ENABLED'] = '0'
from fastapi.testclient import TestClient
import main
import media_tools
from youtube_service import YouTubeService

VIDEO = 'obtCywFHQJ8'


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.service = YouTubeService(self.folder.name)
        self.service.youtube = MagicMock()
        self.service.channel_id = 'UCtest-channel'
        self.service._source = MagicMock(return_value={'snippet':{'title':'Original'},'contentDetails':{'duration':'PT2M'}})
        self.service.get_detailed_analytics = MagicMock(return_value={})
        self.service.get_categorized_channel_videos = MagicMock(return_value={'videos':[{'id':VIDEO,'title':'Original','duration':'PT2M'}],'live':[],'shorts':[]})
        self.service.get_recent_comments_2days = MagicMock(return_value={'active_stream_title':''})
        for name,value in [('yt_service',self.service),('ADMIN_PASSWORD','test-password'),('GEMINI_KEY','private-test-key'),('ai_service',MagicMock(model='saved-model',cached_growth_plan=None))]:
            p = patch.object(main,name,value);p.start();self.addCleanup(p.stop)
        main.active_job['active'] = False
        self.client = TestClient(main.app,raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.client.post('/login',data={'password':'test-password'},follow_redirects=False)

    def upload(self,contents=b'original'):
        return self.client.post('/api/settings/source-recording',data={'video_id':VIDEO},files={'recording':('../../bad-name.mp4',contents,'video/mp4')})

    def test_upload_persists_and_is_channel_scoped_without_exposing_file(self):
        with patch('main.probe_video',return_value=({},120)):
            response = self.upload()
        self.assertEqual(response.status_code,200,response.text)
        path = self.service.source_recording_path(VIDEO)
        self.assertEqual(path.read_bytes(),b'original')
        restarted = YouTubeService(self.folder.name)
        restarted.channel_id = self.service.channel_id
        self.assertEqual(restarted.saved_source_ids(),[VIDEO])
        restarted.channel_id = 'UCother-channel'
        self.assertEqual(restarted.saved_source_ids(),[])
        self.assertEqual(self.client.get('/api/settings/status').json()['saved_source_ids'],[VIDEO])
        self.assertEqual(self.client.get('/source_recordings/UCtest-channel/'+VIDEO+'.mp4').status_code,404)

    def test_wrong_duration_preserves_previous_recording_and_cleans_temporary(self):
        path = self.service.source_recording_path(VIDEO)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous')
        with patch('main.probe_video',return_value=({},30)):
            response = self.upload()
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(path.read_bytes(),b'previous')
        self.assertEqual(list(path.parent.iterdir()),[path])

    def test_invalid_media_preserves_previous_recording(self):
        path = self.service.source_recording_path(VIDEO)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous')
        with patch('main.probe_video',side_effect=RuntimeError('Video validation failed')):
            self.assertEqual(self.upload().status_code,502)
        self.assertEqual(path.read_bytes(),b'previous')

    def test_limits_preserve_old_file(self):
        with patch.object(main,'MAX_SOURCE_BYTES',2):
            self.assertEqual(self.upload().status_code,400)
        with patch.object(main,'MAX_SOURCE_STORAGE_BYTES',2):
            self.assertEqual(self.upload().status_code,400)
        self.assertFalse(self.service.source_recording_path(VIDEO).exists())

    def test_upload_blocked_for_wrong_channel_busy_job_and_cross_origin(self):
        self.service._source.side_effect = ValueError('Select a video owned by the connected channel.')
        self.assertEqual(self.upload().status_code,400)
        self.service._source.side_effect = None
        self.service.creation_lock.acquire()
        try:
            self.assertEqual(self.upload().status_code,400)
        finally:
            self.service.creation_lock.release()
        self.assertEqual(self.client.post('/api/settings/source-recording',headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.post('/api/settings/source-recording',headers={'Content-Length':str(main.MAX_SOURCE_BYTES+2*1024*1024)}).status_code,413)
        self.client.cookies.clear()
        self.assertEqual(self.upload().status_code,401)

    def test_invalid_id_cannot_write_outside_recordings(self):
        with self.assertRaises(ValueError):
            self.service.source_recording_path('../anything')
        self.assertFalse((Path(self.folder.name)/'source_recordings').exists())

    def test_creation_uses_saved_recording_same_offset_without_youtube_download(self):
        path = self.service.source_recording_path(VIDEO)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original')
        self.service.history[VIDEO] = 60
        self.service.upload_video = MagicMock(return_value='new-video')
        self.service.get_or_create_public_playlist = MagicMock(return_value='playlist')
        self.service.add_video_to_playlist = MagicMock()
        self.service._execute = MagicMock(return_value={'items':[{'status':{'privacyStatus':'public'}}]})
        with patch('youtube_service.trim_local') as trim,patch('youtube_service.convert_vertical_clip'),patch('youtube_service.download_clip_robustly') as download:
            result = self.service.create_custom_short_from_video(VIDEO,'Original',MagicMock(),'instant','',lambda _:None)
        self.assertEqual(result,'new-video')
        self.assertEqual(trim.call_args.args[2:4],(60,30))
        download.assert_not_called()

    def test_real_recording_upload_cut_and_vertical_conversion_without_external_post(self):
        recording = Path(self.folder.name)/'original.mp4'
        result = media_tools.run_media([media_tools.get_ffmpeg_path(),'-hide_banner','-loglevel','error','-y',
            '-f','lavfi','-i','testsrc2=size=320x180:rate=30','-t','90','-c:v','libx264','-preset','ultrafast',str(recording)],60)
        self.assertEqual(result.returncode,0,result.stderr)
        self.service._source.return_value['contentDetails']['duration'] = 'PT1M30S'
        self.assertEqual(self.upload(recording.read_bytes()).status_code,200)
        self.service.history[VIDEO] = 30
        validated = []
        def upload(path,*args):
            video,duration = media_tools.probe_video(path)
            validated.append((video['width'],video['height'],round(duration)))
            return 'test-upload'
        self.service.upload_video = MagicMock(side_effect=upload)
        self.service.get_or_create_public_playlist = MagicMock(return_value='test-playlist')
        self.service.add_video_to_playlist = MagicMock()
        self.service._execute = MagicMock(return_value={'items':[{'status':{'privacyStatus':'public'}}]})
        with patch('youtube_service.download_clip_robustly') as download:
            self.assertEqual(self.service.create_custom_short_from_video(VIDEO,'Original',MagicMock(),'instant','',lambda _:None),'test-upload')
            download.assert_not_called()
        self.assertEqual(validated,[(1080,1920,30)])

    def test_auto_selection_prefers_available_recording(self):
        videos = self.service.get_categorized_channel_videos.return_value['videos']
        videos.extend([{'id':'abcdefghijk','title':'Newer','duration':'PT2M','published_at':'2099'}])
        path = self.service.source_recording_path(VIDEO)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original')
        self.assertEqual(self.service.select_source_video_for_shorts()['id'],VIDEO)

    def test_remove_does_not_touch_youtube_video(self):
        path = self.service.source_recording_path(VIDEO)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original')
        self.assertEqual(self.client.post('/api/settings/remove-source-recording',data={'video_id':VIDEO}).status_code,200)
        self.assertFalse(path.exists())
        self.service.youtube.videos.assert_not_called()

    def test_saved_status_rendered_on_new_browser_session_without_returning_key(self):
        self.client.cookies.clear()
        self.client.post('/login',data={'password':'test-password'},follow_redirects=False)
        page = self.client.get('/')
        self.assertEqual(page.status_code,200,page.text[:100])
        self.assertIn('API key saved',page.text)
        self.assertIn('saved-model (saved)',page.text)
        self.assertIn('Source recordings',page.text)
        self.assertNotIn('private-test-key',page.text)


class DownloadBlockTests(unittest.TestCase):
    def test_bot_block_stops_format_retries_and_gives_recovery(self):
        with tempfile.TemporaryDirectory() as folder,patch('media_tools.run_media',return_value=MagicMock(returncode=1,stderr="ERROR: Sign in to confirm you’re not a bot.")) as run:
            with self.assertRaisesRegex(media_tools.SourceAccessError,'Source recordings'):
                media_tools.download_clip_robustly('https://www.youtube.com/watch?v='+VIDEO,0,30,str(Path(folder)/'clip.mp4'),'ffmpeg','.',lambda _:None)
            self.assertEqual(run.call_count,1)


if __name__ == '__main__':
    unittest.main()
