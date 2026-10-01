"""Bounded, validated media processing with useful redacted diagnostics."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class SourceAccessError(RuntimeError):
    """YouTube refused access; repeating format attempts cannot repair a login block."""


def source_download_error(output):
    lowered = (output or '').lower()
    if any(message in lowered for message in ('confirm you’re not a bot', "confirm you're not a bot", 'sign in to confirm', 'private video', 'login required')):
        raise SourceAccessError('YouTube blocked this server from downloading the recording. '
                               'In Settings → Source recordings, select this video and upload its original full recording, '
                               'then retry. The saved recording is used automatically for later Shorts. '
                               'Reconnecting YouTube or changing the Gemini key does not fix this download block.')
    return media_error(output)


def get_ffmpeg_path():
    local = Path(__file__).parent / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    return str(local) if local.exists() else shutil.which('ffmpeg') or 'ffmpeg'


def media_error(output):
    text = re.sub(r'https?://\S+', '[media URL]', output or '')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    important = [line for line in lines if any(word in line.lower() for word in ('error', 'invalid', 'failed', 'denied', 'not found', 'timed out', 'unable', '403', '404'))]
    return ' | '.join((important or lines)[-5:])[-800:] or 'No diagnostic output was returned.'


def run_media(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'Media operation exceeded {timeout} seconds; try a shorter source or check the connection.') from exc
    except FileNotFoundError as exc:
        raise RuntimeError('Required media tool is missing. Check FFmpeg, FFprobe and yt-dlp installation.') from exc


def probe_video(path):
    ffmpeg = Path(get_ffmpeg_path())
    candidate = ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    probe = str(candidate) if candidate.exists() else shutil.which('ffprobe') or str(candidate)
    result = run_media([probe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format', '-of', 'json', str(path)], 30)
    if result.returncode:
        raise RuntimeError('Video validation failed: ' + media_error(result.stderr))
    data = json.loads(result.stdout)
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    duration = float(data.get('format', {}).get('duration', 0))
    if not video or not video.get('width') or not video.get('height') or duration <= 0:
        raise RuntimeError('Downloaded file has no playable video or duration. No upload attempted.')
    return video, duration


def validate_duration(path, expected):
    video, duration = probe_video(path)
    if duration < max(0.5, expected - 2) or duration > expected + 2:
        raise RuntimeError(f'Incomplete clip: received {duration:.1f}s, expected about {expected:.1f}s.')
    return video, duration


def trim_local(source, destination, offset, duration, ffmpeg):
    command = [ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y', '-threads', '2', '-ss', str(offset), '-protocol_whitelist', 'file,pipe', '-i', str(source),
               '-t', str(duration), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', '-c:v', 'libx264', '-threads', '2',
               '-preset', 'veryfast', '-crf', '21', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(destination)]
    result = run_media(command, 600)
    if result.returncode:
        raise RuntimeError('Local clip extraction failed: ' + media_error(result.stderr))
    validate_duration(destination, duration)


def download_clip_robustly(yt_url, offset_sec, duration_sec, out_raw, ffmpeg_bin, ffmpeg_dir, log_fn=print):
    if offset_sec < 0 or not 0 < duration_sec <= 180:
        raise ValueError('Invalid clip offset or duration.')
    base = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-progress', '--socket-timeout', '30',
            '--retries', '2', '--fragment-retries', '2', '--abort-on-unavailable-fragments', '--concurrent-fragments', '1',
            '--ffmpeg-location', ffmpeg_dir, '--merge-output-format', 'mp4', '--force-overwrites']
    if shutil.which('node'):
        base += ['--js-runtimes', 'node']
    if os.getenv('YTDLP_COOKIE_FILE'):
        base += ['--cookies', os.environ['YTDLP_COOKIE_FILE']]
    errors = []
    formats = ('bv*[height<=1080]+ba/b[height<=1080]/best', 'b[height<=720]/bv*[height<=720]+ba/best')
    with tempfile.TemporaryDirectory(prefix='source-download-', dir=Path(out_raw).parent) as work:
        for attempt, fmt in enumerate(formats):
            stage = Path(work)/str(attempt)
            stage.mkdir()
            log_fn(f'Downloading selected segment (attempt {attempt + 1}/2)…')
            command = base + ['-f', fmt, '--download-sections', f'*{offset_sec}-{offset_sec + duration_sec}',
                              '--force-keyframes-at-cuts', '-o', str(stage/'source.%(ext)s'), yt_url]
            try:
                result = run_media(command, 360)
                if result.returncode:
                    raise RuntimeError(source_download_error(result.stderr))
                candidates = [p for p in stage.glob('source.*') if p.suffix in ('.mp4', '.mkv', '.webm')]
                if not candidates:
                    raise RuntimeError('Downloader returned no completed video file.')
                validate_duration(candidates[0], duration_sec)
                shutil.copyfile(candidates[0], out_raw)
                return True
            except SourceAccessError:
                raise
            except (RuntimeError, ValueError) as exc:
                errors.append(str(exc))
                log_fn(f'Segment attempt {attempt + 1} failed: {exc}')
        # Some DASH/HLS sources cannot be remotely cut. Download with the native
        # downloader and seek in the completed local file, keeping the SAME offset.
        log_fn('Remote segment extraction failed. Downloading the source for a local cut (up to 1 GB)…')
        stage = Path(work)/'full'
        stage.mkdir()
        try:
            result = run_media(base + ['-f', formats[1], '--max-filesize', '1G', '-o', str(stage/'source.%(ext)s'), yt_url], 900)
            if result.returncode:
                raise RuntimeError(source_download_error(result.stderr))
            candidates = [p for p in stage.glob('source.*') if p.suffix in ('.mp4', '.mkv', '.webm')]
            if not candidates:
                raise RuntimeError('Source exceeds the download limit or no accessible video format was returned.')
            _, length = probe_video(candidates[0])
            if offset_sec + duration_sec > length + 1:
                raise RuntimeError('Requested segment extends beyond the available source recording.')
            trim_local(candidates[0], out_raw, offset_sec, duration_sec, ffmpeg_bin)
            return True
        except SourceAccessError:
            raise
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    raise RuntimeError('Source download failed after segment and local-cut attempts: ' + errors[-1])


def convert_vertical_clip(raw, output, duration, log_fn=print):
    _, source_duration = probe_video(raw)
    length = min(duration, source_duration)
    if source_duration < max(0.5, duration - 2):
        raise RuntimeError('Source clip is incomplete; download it again before conversion.')
    errors = []
    for width, height, preset in ((1080, 1920, 'veryfast'), (720, 1280, 'ultrafast')):
        filters = f'fps=30,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1'
        command = [get_ffmpeg_path(), '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y', '-threads', '2',
                   '-fflags', '+genpts', '-protocol_whitelist', 'file,pipe', '-i', str(raw), '-t', str(length), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn',
                   '-filter_threads', '1', '-vf', filters, '-c:v', 'libx264', '-threads', '2', '-preset', preset, '-crf', '22',
                   '-c:a', 'aac', '-b:a', '128k', '-af', 'aresample=async=1:first_pts=0', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)]
        try:
            result = run_media(command, 600)
            if result.returncode:
                raise RuntimeError(media_error(result.stderr))
            video, _ = validate_duration(output, length)
            if (video['width'], video['height']) != (width, height):
                raise RuntimeError('Unexpected output dimensions.')
            log_fn(f'Validated vertical clip: {width}×{height}.')
            return width, height
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            log_fn(f'{width}×{height} conversion failed: {exc}')
    raise RuntimeError('Vertical conversion failed at both resolutions. No upload attempted. ' + errors[-1])
