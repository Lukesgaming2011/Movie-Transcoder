import os
import sys
import re
import subprocess
import json
import urllib.request
import urllib.parse
import shutil
import tempfile
import threading
import queue
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- High DPI Awareness for Crisp Windows Rendering ---
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# --- Configuration & Presets Storage ---
IS_FROZEN = getattr(sys, 'frozen', False)
SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable if IS_FROZEN else __file__))
PRESETS_FILE = os.path.join(SCRIPT_DIR, "transcode_movie_presets.json")
DEFAULT_CLI_PATH = ""
DEFAULT_DST_ROOT = os.path.join(os.path.expanduser("~"), "Videos")

DEFAULT_PRESETS = {
    "DVD (2,500 kbps)": {
        "preset": "DVD",
        "video_mode": "custom_bitrate",
        "video_bitrate": "2500",
        "video_rf": "20",
        "audio_mode": "smart_surround",
        "audio_stereo_bitrate": "192",
        "audio_ac3_bitrate": "512",
        "extras_mode": "subfolder_numbered",
        "extras_min_size_mb": 10,
        "extras_dedup": True,
        "extras_include_subfolder": True,
        "remote_scratch": True,
        "dst_root": DEFAULT_DST_ROOT
    },
    "1080p Basic Blu-ray (19,000 kbps)": {
        "preset": "Blue-ray basic",
        "video_mode": "custom_bitrate",
        "video_bitrate": "19000",
        "video_rf": "22",
        "audio_mode": "smart_surround",
        "audio_stereo_bitrate": "192",
        "audio_ac3_bitrate": "512",
        "extras_mode": "subfolder_numbered",
        "extras_min_size_mb": 10,
        "extras_dedup": True,
        "extras_include_subfolder": True,
        "remote_scratch": True,
        "dst_root": DEFAULT_DST_ROOT
    },
    "1080p High Blu-ray (23,000 kbps)": {
        "preset": "Blue-ray",
        "video_mode": "custom_bitrate",
        "video_bitrate": "23000",
        "video_rf": "20",
        "audio_mode": "smart_surround",
        "audio_stereo_bitrate": "192",
        "audio_ac3_bitrate": "512",
        "extras_mode": "subfolder_numbered",
        "extras_min_size_mb": 10,
        "extras_dedup": True,
        "extras_include_subfolder": True,
        "remote_scratch": True,
        "dst_root": DEFAULT_DST_ROOT
    },
    "Automatic (recommended)": {
        "preset": "auto",
        "video_mode": "auto_bitrate",
        "video_bitrate": "19000",
        "video_rf": "22",
        "audio_mode": "smart_surround",
        "audio_stereo_bitrate": "192",
        "audio_ac3_bitrate": "512",
        "extras_mode": "subfolder_numbered",
        "extras_min_size_mb": 10,
        "extras_dedup": True,
        "extras_include_subfolder": True,
        "remote_scratch": True,
        "dst_root": DEFAULT_DST_ROOT
    }
}

# One clear DVD option. Legacy DVD names migrate to 480p.
DEFAULT_PRESETS['DVD 480p (2,500 kbps)'] = dict(
    DEFAULT_PRESETS.pop('DVD (2,500 kbps)'), preset='Fast 480p30')

# Official general-purpose presets, verified against HandBrakeCLI --preset-list.
HANDBRAKE_PROFILES = {
    'HandBrake · 720p': 'Fast 720p30',
    'HandBrake · 1080p': 'Fast 1080p30',
    'HandBrake · 4K (2160p HEVC)': 'Fast 2160p60 4K HEVC',
}
OFFICIAL_PRESETS = ('Fast 480p30', 'Fast 720p30', 'Fast 1080p30',
                    'Fast 2160p60 4K HEVC', 'HQ 720p30 Surround',
                    'HQ 1080p30 Surround', 'HQ 2160p60 4K HEVC Surround')
for profile_name, handbrake_name in HANDBRAKE_PROFILES.items():
    DEFAULT_PRESETS[profile_name] = dict(DEFAULT_PRESETS['Automatic (recommended)'],
                                        preset=handbrake_name, video_mode='preset_default', audio_mode='Preset Audio Default')

# Explicit UHD profiles keep their bitrate separate from the 1080p profiles.
DEFAULT_PRESETS['4K Basic Blu-ray (40,000 kbps)'] = dict(
    DEFAULT_PRESETS['1080p Basic Blu-ray (19,000 kbps)'],
    preset='Fast 2160p60 4K HEVC', video_bitrate='40000')
DEFAULT_PRESETS['4K High Blu-ray (60,000 kbps)'] = dict(
    DEFAULT_PRESETS['1080p High Blu-ray (23,000 kbps)'],
    preset='HQ 2160p60 4K HEVC Surround', video_bitrate='60000')

AUTO_VIDEO_MODE = 'Automatic bitrate (DVD / 1080p / 4K)'
AUTO_VIDEO_MODES = ('auto_bitrate', AUTO_VIDEO_MODE,
                    'Automatic bitrate (DVD 2500 / HD 19000 kbps)')


def automatic_video_bitrate(detected_preset):
    if detected_preset == 'DVD':
        return '2500'
    if detected_preset == 'Fast 2160p60 4K HEVC':
        return '40000'
    return '19000'


DEFAULT_PRESETS['Blu-ray Original Quality (no re-encoding)'] = dict(
    DEFAULT_PRESETS['Automatic (recommended)'], video_mode='original_copy',
    audio_mode='Passthrough (Original Audio Streams)')


def is_original_copy(settings):
    return settings['video'] in ('original_copy', 'Original quality (copy source MKV)')


# Simple two-step output choices; advanced/saved profiles remain available in Settings.
FORMAT_AUTO = 'Automatic (recommended)'
FORMAT_DVD = 'DVD · 480p · MP4'
FORMAT_HD = 'HD · 720p · MP4'
FORMAT_BLURAY = 'Blu-ray · 1080p · MP4'
FORMAT_4K = 'Blu-ray · 4K (2160p) · MP4'
FORMAT_ORIGINAL = 'Original quality · MKV'
FORMAT_CUSTOM = 'Custom preset (advanced)'
RATE_DEFAULT = 'HandBrake default · variable size'
RATE_CUSTOM = 'Custom bitrate (kbps)'
RATE_QUALITY = 'Custom quality (RF / CQ)'
FORMAT_BASE = {FORMAT_DVD: 'Fast 480p30', FORMAT_HD: 'Fast 720p30',
               FORMAT_BLURAY: 'Fast 1080p30', FORMAT_4K: 'Fast 2160p60 4K HEVC'}
OUTPUT_CHOICES = {
    FORMAT_AUTO: {'Recommended by source': 'Automatic (recommended)'},
    FORMAT_DVD: {'Basic · 2,500 kbps': 'DVD 480p (2,500 kbps)', RATE_DEFAULT: None},
    FORMAT_HD: {RATE_DEFAULT: 'HandBrake · 720p'},
    FORMAT_BLURAY: {'Basic · 19,000 kbps': '1080p Basic Blu-ray (19,000 kbps)',
                   'High · 23,000 kbps': '1080p High Blu-ray (23,000 kbps)',
                   RATE_DEFAULT: 'HandBrake · 1080p'},
    FORMAT_4K: {'Basic · 40,000 kbps': '4K Basic Blu-ray (40,000 kbps)',
               'High · 60,000 kbps': '4K High Blu-ray (60,000 kbps)',
               RATE_DEFAULT: 'HandBrake · 4K (2160p HEVC)'},
    FORMAT_ORIGINAL: {'Original bitrate · no encoding': 'Blu-ray Original Quality (no re-encoding)'},
    FORMAT_CUSTOM: {'Use advanced settings': None},
}


def format_from_settings(preset, mode):
    if mode in ('original_copy', 'Original quality (copy source MKV)'):
        return FORMAT_ORIGINAL
    if preset == 'auto':
        return FORMAT_AUTO
    if '2160' in preset:
        return FORMAT_4K
    if '1080' in preset or preset in ('Blue-ray', 'Blue-ray basic'):
        return FORMAT_BLURAY
    if '720' in preset:
        return FORMAT_HD
    if '480' in preset or preset == 'DVD':
        return FORMAT_DVD
    return FORMAT_CUSTOM


ENGINE_AUTO = 'Automatic (GPU preferred)'
ENGINE_NVIDIA = 'NVIDIA GPU (NVENC)'
ENGINE_AMD = 'AMD GPU (VCN)'
ENGINE_INTEL = 'Intel GPU (Quick Sync)'
ENGINE_APPLE = 'Apple GPU (VideoToolbox)'
ENGINE_CPU = 'CPU (software)'
ENGINES = (ENGINE_AUTO, ENGINE_NVIDIA, ENGINE_AMD, ENGINE_INTEL, ENGINE_APPLE, ENGINE_CPU)
ENGINE_NAMES = {ENGINE_AUTO: 'Auto GPU / CPU', ENGINE_NVIDIA: 'NVIDIA NVENC',
                ENGINE_AMD: 'AMD VCN', ENGINE_INTEL: 'Intel Quick Sync',
                ENGINE_APPLE: 'Apple VideoToolbox', ENGINE_CPU: 'CPU'}
ENCODERS = {
    ENGINE_NVIDIA: ('nvenc_h264', 'nvenc_h265_10bit'),
    ENGINE_AMD: ('vce_h264', 'vce_h265_10bit'),
    ENGINE_INTEL: ('qsv_h264', 'qsv_h265_10bit'),
    ENGINE_APPLE: ('vt_h264', 'vt_h265_10bit'),
    ENGINE_CPU: ('x264', 'x265_10bit'),
}
_encoder_cache = {}
_encoder_lock = threading.Lock()


def engine_for(settings, job=None):
    return (job or {}).get('engine', settings.get('engine', ENGINE_CPU))


def uses_nvidia(settings):
    return engine_for(settings) == ENGINE_NVIDIA and not is_original_copy(settings)


def uses_hardware(settings, job=None):
    return engine_for(settings, job) in ENCODERS and engine_for(settings, job) != ENGINE_CPU and not is_original_copy(settings)


def parse_available_encoders(text):
    # --help lists only encoders available in this build on this machine.
    match = re.search(r'--encoder <string>[^\n]*\n((?:[ \t]+[a-z][a-z0-9_]*[ \t]*\r?\n)+)', text)
    return set(match.group(1).split()) if match else set()


def probe_encoders(cli, cancel=None):
    if cancel and cancel():
        raise InterruptedError('Encoder check cancelled.')
    key = (os.path.realpath(cli), os.stat(cli).st_mtime_ns)
    with _encoder_lock:
        cached = _encoder_cache.get(key)
    if cached is not None:
        return set(cached)
    result = subprocess.run([cli, '--help'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if cancel and cancel():
        raise InterruptedError('Encoder check cancelled.')
    encoders = parse_available_encoders(result.stdout)
    if not encoders:
        raise RuntimeError('Could not read HandBrake encoders. Choose a current HandBrakeCLI in Tool setup.')
    with _encoder_lock:
        _encoder_cache[key] = frozenset(encoders)
    return encoders


def select_encoder(settings, job, available):
    hevc = 'HEVC' in job['preset'] or '2160' in job['preset']
    requested = settings.get('engine', ENGINE_AUTO)
    choices = list(ENCODERS) if requested == ENGINE_AUTO else [requested]
    if settings.get('fallback', True) and ENGINE_CPU not in choices:
        choices.append(ENGINE_CPU)
    for engine in choices:
        encoder = ENCODERS.get(engine, ('', ''))[int(hevc)]
        if encoder in available:
            return engine, encoder
    raise RuntimeError('The selected encoder is unavailable for this profile. Choose Automatic or CPU in Picture & sound.')


def hardware_encoding_args(settings, job):
    if is_original_copy(settings):
        return []
    engine = engine_for(settings, job)
    if engine not in ENCODERS:
        return []  # Automatic is resolved in a background worker before encoding.
    hevc = 'HEVC' in job['preset'] or '2160' in job['preset']
    encoder = job.get('encoder', ENCODERS[engine][int(hevc)])
    args = ['-e', encoder]
    if engine == ENGINE_NVIDIA:
        args += ['--encoder-preset', 'slow', '--enable-hw-decoding', 'nvdec']
    elif engine == ENGINE_AMD:
        args += ['--encoder-preset', 'quality']
    elif engine == ENGINE_INTEL:
        args += ['--encoder-preset', 'balanced', '--enable-hw-decoding', 'qsv']
    elif engine == ENGINE_CPU:
        args += ['--encoder-preset', 'medium']
        if settings.get('responsive', True):
            threads = max(1, min(8, (os.cpu_count() or 2) // 2))
            args += ['-x', ('pools=' if hevc else 'threads=') + str(threads)]
    return args


def video_encoding_args(settings, job):
    mode = settings['video']
    passes = ['--no-multi-pass'] if uses_hardware(settings, job) else ['--multi-pass']
    if mode in AUTO_VIDEO_MODES:
        return ['-b', automatic_video_bitrate(job.get('detected_preset', job['preset']))] + passes
    if mode == 'custom_bitrate' or 'Bitrate (kbps)' in mode:
        return ['-b', settings['bitrate']] + passes
    if mode == 'custom_rf' or 'Constant Quality' in mode:
        return ['-q', settings['rf']] + (['--no-multi-pass'] if uses_hardware(settings, job) else [])
    return ['--no-multi-pass'] if uses_hardware(settings, job) else []


def queue_profile_label(profile, job, settings):
    if is_original_copy(settings):
        return 'Original Quality · Exact MKV copy'
    if profile == 'Automatic (recommended)':
        detected = job.get('detected_preset', job['preset'])
        profile = ('DVD' if detected == 'DVD' else '4K Basic Blu-ray'
                   if detected == 'Fast 2160p60 4K HEVC' else '1080p Basic Blu-ray')
    profile = re.sub(r' \([\d,]+ kbps\)$', '', profile)
    aliases = {'DVD': 'Fast 480p30', 'Blue-ray basic': 'Fast 1080p30',
               'Blue-ray': 'HQ 1080p30 Surround'}
    base = aliases.get(job['preset'], job['preset'])
    return profile + ' · ' + base + ' · ' + ENGINE_NAMES.get(engine_for(settings, job), 'Auto GPU / CPU')


# --- Core Transcoding Logic Helpers ---
def lookup_movie_year(movie_name):
    try:
        query = re.sub(r'[^a-zA-Z0-9\s]', '', movie_name).strip()
        query = re.sub(r'\s+', '_', query).lower()
        if not query:
            return None
        first_letter = query[0]
        url = f"https://sg.media-imdb.com/suggests/{first_letter}/{query}.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data_str = response.read().decode('utf-8')
            first_paren = data_str.find('(')
            last_paren = data_str.rfind(')')
            if first_paren != -1 and last_paren != -1:
                json_data = json.loads(data_str[first_paren + 1:last_paren])
                results = json_data.get("d", [])
                if results:
                    for res in results:
                        if "y" in res and res.get("q") in (None, "feature", "movie"):
                            return str(res["y"])
    except Exception:
        pass
    return None

def scan_file(file_path, cli_path, cancelled=lambda: False):
    command = [cli_path, "-i", file_path, "--scan"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        deadline = time.monotonic() + 180
        while True:
            if cancelled() or time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                if cancelled():
                    raise InterruptedError('Scan cancelled.')
                raise TimeoutError('HandBrake scan timed out after three minutes.')
            try:
                stdout, stderr = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                continue
        output = (stderr + stdout).decode('utf-8', 'replace')
        if process.returncode != 0 or not re.search(r'\+\s*size:\s*\d+x\d+', output):
            raise RuntimeError('Cannot scan this movie: ' + output[-1200:])
        return output


def parse_audio_tracks(scan_output):
    tracks = []
    audio_section_match = re.search(r'\+ audio tracks:\s*\n((?:\s*\+\s*\d+,\s*.*\n)+)', scan_output)
    if audio_section_match:
        section_text = audio_section_match.group(1)
        for line in section_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            match = re.match(r'\+\s*(\d+),\s*(.*)', line)
            if match:
                idx = int(match.group(1))
                desc = match.group(2)
                is_5point1 = any(ch in desc for ch in ("5.1 ch", "6 ch", "7.1 ch", "8 ch"))
                lang_match = re.search(r'\(iso639-2:\s*([a-z]{3})\)', desc)
                lang = lang_match.group(1) if lang_match else ""
                tracks.append({
                    'index': idx,
                    'desc': desc,
                    'is_5point1': is_5point1,
                    'lang': lang
                })
    return tracks

def detect_preset_from_scan(scan_output):
    match = re.search(r'\+\s*size:\s*(\d+)x(\d+)', scan_output)
    if match:
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 1920 or height > 1080:
            return "Fast 2160p60 4K HEVC", f"{width}x{height} (above HD)"
        if width > 720 or height > 576:
            return "Blue-ray basic", f"{width}x{height} (HD)"
        else:
            return "DVD", f"{width}x{height} (SD)"
    return "DVD", "Unknown"

def is_remote_path(path):
    path = os.path.abspath(path)
    if path.startswith(r"\\") or path.startswith("//"):
        return True
    try:
        drive, _ = os.path.splitdrive(path)
        if drive and drive.endswith(":"):
            drive_root = drive + "\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_root)
            if drive_type == 4:  # DRIVE_REMOTE
                return True
    except Exception:
        pass
    return False

def format_size(bytes_num):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


import base64
import hashlib
import struct
import wave
import urllib.error
from tkinter import simpledialog


class AudioCD:
    """Windows CD-DA reader. Standard audio CDs only; no secure-rip verification."""
    def __init__(self, drive):
        import ctypes
        from ctypes import wintypes
        if os.name != 'nt' or not re.fullmatch(r'[A-Za-z]:', drive):
            raise ValueError('Choose a Windows CD drive, for example D:.')
        self.ct = ctypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.api.DeviceIoControl.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateFileW('\\\\.\\' + drive, 0x80000000, 3, None, 3, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

    def ioctl(self, code, data=None, size=804):
        ct = self.ct
        output = ct.create_string_buffer(size)
        count = ct.c_ulong()
        source = ct.create_string_buffer(data) if data is not None else None
        if not self.api.DeviceIoControl(self.handle, code, source, len(data) if data else 0,
                                       output, size, ct.byref(count), None):
            raise ct.WinError(ct.get_last_error())
        return output.raw[:count.value]

    def toc(self):
        raw = self.ioctl(0x24000)  # IOCTL_CDROM_READ_TOC
        if len(raw) < 4:
            raise ValueError('The drive did not return a CD table of contents.')
        first, last = raw[2:4]
        count = last - first + 1
        if not 1 <= first <= last <= 99 or len(raw) < 4 + (count + 1) * 8:
            raise ValueError('Invalid audio CD table of contents.')
        entries = [raw[4 + i * 8:12 + i * 8] for i in range(count + 1)]
        if any(e[1] & 4 for e in entries[:-1]):
            raise ValueError('This is a data or mixed-mode disc. Insert a standard audio CD.')
        offsets = [(e[5] * 60 + e[6]) * 75 + e[7] for e in entries]
        if offsets[0] < 150 or any(b <= a for a, b in zip(offsets, offsets[1:])):
            raise ValueError('Invalid CD track offsets.')
        return {'first': first, 'last': last, 'offsets': offsets}

    def read(self, sector, count):
        # DiskOffset uses 2048-byte units; CDDA output uses 2352-byte sectors.
        data = struct.pack('<qII', sector * 2048, count, 2)
        result = self.ioctl(0x2403e, data, count * 2352)
        if len(result) != count * 2352:
            raise OSError('Short audio read; the disc may be damaged.')
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.api.CloseHandle(self.handle)


def cd_disc_id(toc):
    offsets = [0] * 100
    offsets[0] = toc['offsets'][-1]
    for number, offset in zip(range(toc['first'], toc['last'] + 1), toc['offsets'][:-1]):
        offsets[number] = offset
    value = f"{toc['first']:02X}{toc['last']:02X}" + ''.join(f'{n:08X}' for n in offsets)
    return base64.b64encode(hashlib.sha1(value.encode('ascii')).digest()).decode().translate(
        str.maketrans('+/=', '._-'))


def cd_artist(credits):
    return ''.join(c.get('name', c.get('artist', {}).get('name', '')) + c.get('joinphrase', '')
                   for c in credits if isinstance(c, dict))


_cd_lookup_lock = threading.Lock()
_cd_lookup_time = 0.0


def cd_lookup(toc):
    global _cd_lookup_time
    disc_id = cd_disc_id(toc)
    url = 'https://musicbrainz.org/ws/2/discid/' + disc_id + '?fmt=json&inc=artists+recordings&cdstubs=no'
    contact = re.sub(r'[\r\n]', '', os.environ.get('LIBRARY_STUDIO_CONTACT', 'desktop audio CD application'))[:200]
    request = urllib.request.Request(url, headers={'User-Agent': f'LibraryStudio/{APP_VERSION} ({contact})',
                                                   'Accept': 'application/json'})
    with _cd_lookup_lock:
        time.sleep(max(0, 1.1 - (time.monotonic() - _cd_lookup_time)))
        _cd_lookup_time = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return []
            raise
    matches = []
    for release in payload.get('releases', []):
        for medium in release.get('media', []):
            tracks = medium.get('tracks', [])
            if (any(d.get('id') == disc_id for d in medium.get('discs', []))
                    and len(tracks) == len(toc['offsets']) - 1):
                matches.append({'album': release.get('title', ''), 'artist': cd_artist(release.get('artist-credit', [])),
                                'date': release.get('date', ''), 'disc': str(medium.get('position', 1)),
                                'country': release.get('country', ''), 'id': release.get('id', ''),
                                'tracks': [{'title': t.get('title') or t.get('recording', {}).get('title', ''),
                                            'artist': cd_artist(t.get('artist-credit') or t.get('recording', {}).get('artist-credit', []))}
                                           for t in tracks]})
    return matches


def cd_safe_name(value):
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', value).strip().rstrip('.')[:100].rstrip(' .') or 'Unknown'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        value = '_' + value
    return value


def cd_tag_wav(path, metadata):
    """Write RIFF INFO without altering PCM; JSON sidecar preserves full Unicode metadata."""
    tags = {'INAM': 'title', 'IART': 'artist', 'IPRD': 'album', 'ICRD': 'date', 'ITRK': 'track'}
    chunks = b'INFO'
    for tag, key in tags.items():
        value = str(metadata.get(key, '')).encode('utf-8') + b'\0'
        chunks += tag.encode('ascii') + struct.pack('<I', len(value)) + value + b'\0' * (len(value) % 2)
    with open(path, 'r+b') as file:
        file.seek(0, 2)
        file.write(b'LIST' + struct.pack('<I', len(chunks)) + chunks)
        length = file.tell()
        file.seek(4)
        file.write(struct.pack('<I', length - 8))


import glob
import zipfile
import platform
import webbrowser
from pathlib import Path
from collections import deque

APP_NAME = 'Library Studio'
APP_VERSION = '0.9.1'


def format_eta(seconds):
    """Round estimates up so a job never promises zero seconds while still working."""
    seconds = max(1, int(seconds + .999))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f'{hours}h {minutes:02d}m {seconds:02d}s'
    if minutes:
        return f'{minutes}m {seconds:02d}s'
    return f'{seconds}s'


def encoding_progress(line):
    """HandBrake percentages restart for each pass; combine all tasks into file progress."""
    match = re.search(r'Encoding: task (\d+) of (\d+),\s*([\d.]+)\s*%', line)
    if not match:
        return None
    task, tasks = int(match[1]), max(1, int(match[2]))
    percent = min(100., max(0., float(match[3])))
    return min(100., max(0., ((task - 1) + percent / 100) * 100 / tasks))


class ConversionETA:
    """Estimate from observed file progress; source sizes weight pending queue work."""
    def __init__(self, jobs, clock=time.monotonic):
        self.clock = clock
        self.began = clock()
        self.weights = []
        for job in jobs:
            try:
                size = max(1, os.path.getsize(job['src']))
            except OSError:
                size = 1
            self.weights.append(0 if os.path.exists(job['dst']) else size)
        self.index = None
        self.started = self.began
        self.rate = None
        self.sample_time = self.began
        self.last_text = None

    def start(self, index):
        # Reset on every file or CPU retry; neither inherits another encoder's speed.
        self.index = index
        self.started = self.sample_time = self.clock()
        self.rate = None
        return self.text(0)

    def text(self, percent):
        now = self.clock()
        elapsed = max(0., now - self.started)
        fraction = min(1., max(0., percent / 100))
        if elapsed >= 3 and fraction >= .002 and self.index is not None:
            observed = elapsed / fraction
            # Time-based smoothing avoids flicker and depends on time, not update frequency.
            blend = min(1., max(0., now - self.sample_time) / 4)
            self.rate = observed if self.rate is None else self.rate + blend * (observed - self.rate)
            self.sample_time = now
        current = 'Finalizing…' if fraction >= 1 else 'Calculating…'
        queue_left = 'Calculating…'
        if self.rate is not None and self.index is not None:
            left = max(0., self.rate * (1 - fraction))
            future = sum(self.weights[self.index + 1:]) / max(1, self.weights[self.index]) * self.rate
            if fraction < 1:
                current = '~' + format_eta(left)
            queue_left = '~' + format_eta(left + future) if left + future > 0 else 'Finalizing…'
        return f'Time left · File: {current}  ·  Queue: {queue_left}'

    def complete(self, index):
        self.weights[index] = 0


def queue_eta(app, text):
    """Pass immutable estimates to Tk; the worker alone owns the estimator."""
    label = getattr(app, 'lbl_eta', None)
    if label is not None:
        app.ui_queue.put(lambda text=text: label.configure(text=text))
UI_FONT = 'Segoe UI' if os.name == 'nt' else 'Helvetica' if sys.platform == 'darwin' else 'DejaVu Sans'
UI_BOLD = 'Segoe UI Semibold' if os.name == 'nt' else UI_FONT
MONO_FONT = 'Consolas' if os.name == 'nt' else 'Menlo' if sys.platform == 'darwin' else 'DejaVu Sans Mono'
BG = '#e8edf2'
INK = '#263747'
MUTED = '#627487'
ACCENT = '#276c69'


CUSTOM_PRESET = 'Custom…'
RETIRED_PRESETS = {
    'DVD (2500 kbps)', 'DVD (2,500 kbps)', 'DVD 576p (2,500 kbps)',
    'Blu-ray Basic (19,000 kbps)', 'Blu-ray High Quality (23,000 kbps)',
    "Blu-ray (6500 kbps)", "Blu-ray High Quality (RF 20 · variable bitrate)",
    'DVD · balanced', 'Blu-ray · balanced', 'Blu-ray · high quality',
    'DVD · compact (2500 kbps)', 'Blu-ray · compact (6500 kbps)',
    'DVD (Preset Default)', 'Blue-ray Basic (Preset Default)', 'Blue-ray High Quality',
    'Auto-Detect (SD -> DVD, HD -> Blu-ray)', 'DVD (Custom 2500 kbps)', 'Blu-ray (Custom 6500 kbps)'
}
PALETTES = {
    'light': dict(bg='#f0f3f5', surface='#ffffff', ink='#1d1d1f', muted='#6e6e73', accent='#087f8c',
                  shadow='#e6e6e9', shadow_soft='#eeeef0', highlight='#ffffff', hover='#f0f0f3',
                  accent_hover='#056a75', success='#248a3d', danger='#d70015', border='#dedee3',
                  entry='#f9f9fb', pressed='#e5e5ea', disabled='#96969e', selection='#d7eef0',
                  heading='#f5f5f7', warning='#986000'),
    'dark': dict(bg='#1c1c1e', surface='#2c2c2e', ink='#f5f5f7', muted='#aeaeb6', accent='#087f8c',
                 shadow='#151517', shadow_soft='#202022', highlight='#3a3a3c', hover='#3a3a3e',
                 accent_hover='#0b929f', success='#63d77a', danger='#ff6961', border='#48484c',
                 entry='#242426', pressed='#202022', disabled='#85858d', selection='#244f55',
                 heading='#323235', warning='#ffd060')
}
COLORS = PALETTES['light']


def set_palette(mode):
    global COLORS, BG, INK, MUTED, ACCENT
    COLORS = PALETTES[mode]
    BG, INK, MUTED, ACCENT = (COLORS[k] for k in ('bg', 'ink', 'muted', 'accent'))


def data_directory():
    if '--self-test-report' in sys.argv:
        index = sys.argv.index('--self-test-report')
        test_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[index+1])), 'self-test-data')
        os.makedirs(test_dir, exist_ok=True)
        return test_dir
    override = os.environ.get('LIBRARY_STUDIO_DATA_DIR')
    local = os.path.join(SCRIPT_DIR, 'LibraryStudio-data')
    # Existing portable installations retain their settings and managed tools.
    if override:
        target = os.path.abspath(os.path.expanduser(override))
    elif os.path.isdir(local) or os.path.isfile(os.path.join(SCRIPT_DIR, 'portable.flag')):
        target = local
    elif sys.platform == 'win32':
        target = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'LibraryStudio')
    elif sys.platform == 'darwin':
        target = os.path.expanduser('~/Library/Application Support/LibraryStudio')
    else:
        target = os.path.join(os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share')), 'LibraryStudio')
    try:
        os.makedirs(target, exist_ok=True)
        with tempfile.TemporaryFile(dir=target):
            pass
        return target
    except OSError:
        if override:
            raise
        fallback = os.path.join(os.path.expanduser('~'), '.library-studio')
        os.makedirs(fallback, exist_ok=True)
        return fallback


DATA_DIR = data_directory()
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
TOOLS_DIR = os.path.join(DATA_DIR, 'tools')


def read_settings():
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def find_tool(name):
    filename = name + ('.exe' if os.name == 'nt' else '')
    saved = read_settings().get(name, '')
    candidates = [os.path.join(TOOLS_DIR, name, filename), os.path.join(SCRIPT_DIR, filename), saved,
                  shutil.which(filename) or '', os.path.join(os.environ.get('ProgramFiles', ''), 'HandBrake', filename)]
    if name == 'HandBrakeCLI':
        candidates += glob.glob(os.path.join(os.path.expanduser('~'), 'Downloads', 'HandBrakeCLI*', filename))
    return next((p for p in candidates if p and os.path.isfile(p) and (os.name == 'nt' or os.access(p, os.X_OK))), '')


def open_folder(path):
    if os.name == 'nt':
        os.startfile(path)
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def publish_file(source, destination):
    """Publish a completed local file without replacing an existing library item."""
    if os.name == 'nt':
        os.rename(source, destination)  # Windows rename already rejects existing destinations.
    else:
        os.link(source, destination)  # Atomic and exclusive on native macOS/Linux filesystems.
        os.unlink(source)


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [x1+radius,y1, x2-radius,y1, x2,y1, x2,y1+radius, x2,y2-radius,
              x2,y2, x2-radius,y2, x1+radius,y2, x1,y2, x1,y2-radius, x1,y1+radius, x1,y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)



def soft_button_image(master, color=None, pressed=False):
    """Small rounded ttk surface; transparent corners work on every panel."""
    import math
    color = color or COLORS['surface']
    width, height = 64, 36
    image = tk.PhotoImage(master=master, width=width, height=height)
    face = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    edge = tuple(int(COLORS['border'][i:i+2], 16) for i in (1, 3, 5))
    primary = color in (ACCENT, COLORS['accent_hover'])
    for y in range(height):
        for x in range(width):
            qx, qy = abs(x-31.5)-23, abs(y-17.5)-9
            d = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 7
            if d > 0:
                continue
            rgb = face if primary or d < -1 else edge
            if pressed:
                rgb = tuple(max(0, v-10) for v in rgb)
            image.put('#%02x%02x%02x' % rgb, (x, y))
    return image


def configure_soft_buttons(master, style):
    if not hasattr(master, 'soft_images'):
        master.soft_images = {}
    for name, color in [('Soft', COLORS['surface']), ('Primary', ACCENT)]:
        element = master.theme_mode + '.' + name + '.surface'
        if element not in master.soft_images:
            images = [soft_button_image(master, color), soft_button_image(master, color, True),
                      soft_button_image(master, COLORS['hover'] if name == 'Soft' else COLORS['accent_hover']),
                      soft_button_image(master, BG)]
            master.soft_images[element] = images
            style.element_create(element, 'image', images[0], ('disabled', images[3]),
                                 ('pressed', images[1]), ('active', images[2]), border=(12, 10), sticky='nsew')
        layout = [(element, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.label', {'sticky': 'nsew'})]})]})]
        if name == 'Soft':
            style.layout('TButton', layout)
        else:
            style.layout('Accent.TButton', layout)
            style.layout('Success.TButton', layout)
    style.map('Success.TButton', foreground=[('disabled', MUTED)])
    style.map('Accent.TButton', foreground=[('disabled', MUTED)])


def cancel_widget_timers(widget):
    commands = {}
    def collect(current):
        commands.update({name: current for name in current._tclCommands or []})
        for child in current.winfo_children():
            collect(child)
    collect(widget)
    for timer in widget.tk.call('after', 'info'):
        try:
            script, _ = widget.tk.call('after', 'info', timer)
            command = str(script).split()[0]
            if command in commands:
                commands[command].after_cancel(timer)
        except (tk.TclError, IndexError):
            pass


def smooth_ease(t):
    return t*t*t*(t*(t*6-15)+10)


class Motion:
    """One shared frame timer; interrupted animations retain their displayed value."""
    def __init__(self, owner):
        self.owner = owner
        self.running = {}
        self.timer = None

    def cancel(self, key, complete=True):
        item = self.running.pop(key, None)
        if item and complete:
            item['update'](item['end'])
            if item['finish'] is not None:
                item['finish']()
        if not self.running and self.timer is not None:
            self.owner.after_cancel(self.timer)
            self.timer = None

    def reduced(self):
        preference = getattr(self.owner, 'reduce_motion', None)
        return preference is not None and preference.get()

    def tween(self, key, start, end, update, duration=.26, finish=None, ease=smooth_ease):
        self.cancel(key, complete=False)
        if self.reduced() or start == end or not self.owner.winfo_ismapped():
            update(end)
            if finish:
                finish()
            return
        item = dict(began=time.perf_counter(), start=start, end=end, update=update,
                    duration=max(.001, duration), finish=finish, ease=ease)
        self.running[key] = item
        update(start)
        if self.timer is None:
            self.timer = self.owner.after(16, self.step)

    def step(self):
        self.timer = None
        began = time.perf_counter()
        for key, item in list(self.running.items()):
            if self.running.get(key) is not item:
                continue
            t = min(1., max(0., (began-item['began'])/item['duration']))
            item['update'](item['start']+(item['end']-item['start'])*item['ease'](t))
            if t >= 1 and self.running.get(key) is item:
                self.running.pop(key, None)
                if item['finish'] is not None:
                    item['finish']()
        if self.running and self.timer is None:
            # Account for drawing time so active animations share a steady frame cadence.
            delay = max(1, 16 - int((time.perf_counter()-began)*1000))
            self.timer = self.owner.after(delay, self.step)

    def settle(self):
        for key in list(self.running):
            self.cancel(key)


class SoftCard(tk.Canvas):
    """A quiet rounded panel with a fine border and a restrained shadow."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, height=90, **kwargs)
        self.body = ttk.Frame(self, padding=(18, 16))
        self.inset = 15
        self.window = self.create_window(17, 15, window=self.body, anchor='nw')
        self.bind('<Configure>', self.draw)
        self.body.bind('<Configure>', self.fit)

    def fit(self, event=None):
        self.configure(height=self.body.winfo_reqheight() + self.inset * 2)

    def draw(self, event=None):
        self.delete('surface')
        w, h = self.winfo_width(), self.winfo_height()
        for offset, color in [(2, COLORS['shadow_soft']), (0, COLORS['surface'])]:
            round_rect(self, 7+offset, 7+offset, w-8+offset, h-8+offset, 20,
                       fill=color, outline=COLORS['border'] if offset == 0 else '', tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window, width=max(20, w-34))


class Foldout(ttk.Frame):
    def __init__(self, parent, title, subtitle='', opened=False):
        super().__init__(parent)
        self.opened = opened
        self.animation = None
        self.title_text = title
        self.heading = ttk.Button(self, text=('−  ' if opened else '+  ') + title + ('   ·   ' + subtitle if subtitle else ''),
                                  style='Fold.TButton', command=self.toggle)
        self.heading.pack(fill='x', pady=(2, 0))
        self.clip = tk.Frame(self, bg=BG, height=1)
        self.clip.pack(fill='x')
        self.clip.pack_propagate(False)
        self.body = ttk.Frame(self.clip, padding=(12, 8, 12, 12))
        self.body.place(x=0, y=0, relwidth=1)
        self.subtitle = subtitle
        self.after_idle(self.refresh)

    def refresh(self):
        if self.opened:
            self.clip.configure(height=self.body.winfo_reqheight())

    def toggle(self):
        owner = self.winfo_toplevel()
        key = 'foldout:' + str(self)
        start = self.clip.winfo_height()
        owner.motion.cancel(key)
        self.opened = not self.opened
        self.heading.configure(text=('−  ' if self.opened else '+  ') + self.title_text +
                               ('   ·   ' + self.subtitle if self.subtitle else ''))
        end = self.body.winfo_reqheight() if self.opened else 1
        owner.motion.tween(key, start, end, lambda h: self.clip.configure(height=round(h)),
                           finish=lambda: self.clip.configure(height=end))


class ScrollArea(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=bar.set)
        self.body = ttk.Frame(self.canvas, style='Chrome.TFrame')
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        # Bind to this toplevel only; do not intercept wheel scrolling in other windows.
        self.winfo_toplevel().bind('<MouseWheel>', self.wheel, add='+')
        self.winfo_toplevel().bind('<Button-4>', self.wheel, add='+')
        self.winfo_toplevel().bind('<Button-5>', self.wheel, add='+')

    def wheel(self, event):
        widget = event.widget
        if isinstance(widget, (ttk.Treeview, tk.Text, ttk.Combobox)):
            return
        while widget:
            if widget == self:
                amount = (-1 if event.num == 4 else 1) if event.num in (4, 5) else -int(event.delta / (1 if sys.platform == 'darwin' else 120))
                self.canvas.yview_scroll(amount, 'units')
                break
            widget = getattr(widget, 'master', None)


def download_file(url, path, cancel, report, limit=450*1024*1024):
    request = urllib.request.Request(url, headers={'User-Agent': 'LibraryStudio/' + APP_VERSION})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=30) as response, open(path, 'wb') as file:
        total = int(response.headers.get('Content-Length') or 0)
        if total > limit:
            raise ValueError('The download exceeds the supported package size.')
        while True:
            if cancel.is_set():
                raise InterruptedError('Setup cancelled.')
            block = response.read(256 * 1024)
            if not block:
                break
            size += len(block)
            if size > limit:
                raise ValueError('The download exceeds the supported package size.')
            digest.update(block)
            file.write(block)
            report(f'Downloading {size // 1048576} MB' + (f' / {total // 1048576} MB' if total else ''))
    return digest.hexdigest()


def tool_release(name):
    if name == 'ffmpeg':
        base = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        with urllib.request.urlopen(base + '.sha256', timeout=20) as response:
            checksum = response.read(4096).decode().split()[0]
        return base, checksum
    request = urllib.request.Request('https://api.github.com/repos/HandBrake/HandBrake/releases/latest',
                                     headers={'User-Agent': 'LibraryStudio/' + APP_VERSION})
    with urllib.request.urlopen(request, timeout=20) as response:
        release = json.load(response)
    asset = next((a for a in release['assets'] if re.search(r'HandBrakeCLI-.*win-x86_64\.zip$', a['name'])), None)
    if not asset:
        raise RuntimeError('No supported HandBrake Windows package found. Choose an existing executable instead.')
    digest = asset.get('digest') or ''
    if not digest.startswith('sha256:'):
        raise RuntimeError('HandBrake did not publish a SHA-256 digest. Use the official download link instead.')
    return asset['browser_download_url'], digest.split(':', 1)[1]


def install_tool(name, cancel, report):
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Automatic tool setup supports Windows x64. Browse to compatible tools on this computer.')
    url, checksum = tool_release(name)
    if not re.fullmatch('[0-9a-fA-F]{64}', checksum):
        raise ValueError('Invalid package checksum. Setup stopped.')
    os.makedirs(TOOLS_DIR, exist_ok=True)
    target = os.path.join(TOOLS_DIR, name)
    if os.path.exists(target):
        raise FileExistsError(f'{target} already exists. Use its executable or choose another portable folder.')
    with tempfile.TemporaryDirectory(prefix='.download-', dir=TOOLS_DIR) as work:
        package = os.path.join(work, 'package.zip')
        actual = download_file(url, package, cancel, report)
        if actual.lower() != checksum.lower():
            raise ValueError('Checksum mismatch. Download discarded; retry setup.')
        stage = os.path.join(work, 'verified')
        os.makedirs(stage)
        wanted = {name.lower() + '.exe'}
        if name == 'ffmpeg':
            wanted.add('ffprobe.exe')
        with zipfile.ZipFile(package) as archive:
            seen = set()
            for member in archive.infolist():
                leaf = member.filename.replace('\\', '/').rsplit('/', 1)[-1]
                is_license = leaf.lower().startswith(('license', 'copying', 'notice'))
                if member.is_dir() or (leaf.lower() not in wanted and not is_license):
                    continue
                if cancel.is_set():
                    raise InterruptedError('Setup cancelled.')
                if member.file_size > 350 * 1024 * 1024 or leaf.lower() in seen:
                    raise ValueError('Unexpected package contents.')
                seen.add(leaf.lower())
                with archive.open(member) as src, open(os.path.join(stage, leaf), 'xb') as dst:
                    shutil.copyfileobj(src, dst)
            if name.lower() + '.exe' not in seen:
                raise ValueError('The package did not contain the expected executable.')
        write_json_atomic(os.path.join(stage, 'source.json'), {'url': url, 'sha256': actual, 'downloaded': datetime.now().isoformat()})
        if cancel.is_set():
            raise InterruptedError('Setup cancelled.')
        os.rename(stage, target)
    return os.path.join(target, name + '.exe')


def make_portable(folder):
    """Copy the single-file application plus managed tools, without machine-specific settings."""
    target = os.path.join(folder, 'Library Studio')
    if os.path.exists(target):
        raise FileExistsError('That folder already contains Library Studio. Choose another location.')
    os.makedirs(folder, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.library-studio-', dir=folder) as temporary:
        stage = os.path.join(temporary, 'app')
        os.makedirs(stage)
        shutil.copy2(sys.executable if IS_FROZEN else os.path.abspath(__file__),
                     os.path.join(stage, ('LibraryStudio.exe' if os.name == 'nt' else 'LibraryStudio') if IS_FROZEN else 'LibraryStudio.py'))
        if os.path.isdir(TOOLS_DIR):
            shutil.copytree(TOOLS_DIR, os.path.join(stage, 'LibraryStudio-data', 'tools'),
                            ignore=shutil.ignore_patterns('.download-*'))
        if os.path.isfile(PRESETS_FILE):
            # Export profiles without the original computer's destination paths.
            with open(PRESETS_FILE, encoding='utf-8') as file:
                profiles = json.load(file)
            for value in profiles.values():
                value.pop('dst_root', None)
            write_json_atomic(os.path.join(stage, 'LibraryStudio-data', 'presets.json'), profiles)
        launcher = '@echo off\ncd /d "%~dp0"\nwhere py >nul 2>nul\nif not errorlevel 1 (\n  py -3 "LibraryStudio.py"\n  goto done\n)\nwhere python >nul 2>nul\nif not errorlevel 1 (\n  python "LibraryStudio.py"\n  goto done\n)\necho Install Python 3.10 or later with Tcl/Tk from https://www.python.org/downloads/windows/\n:done\nif errorlevel 1 pause\n'
        if IS_FROZEN:
            launcher = '@echo off\nstart "" "%~dp0LibraryStudio.exe"\n'
        if os.name == 'nt':
            Path(stage, 'Start Library Studio.cmd').write_text(launcher, encoding='utf-8')
        else:
            launcher_path = Path(stage, 'Start Library Studio.sh')
            launcher_path.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")"\n' + ('exec ./LibraryStudio\n' if IS_FROZEN else 'exec python3 LibraryStudio.py\n'), encoding='utf-8')
            launcher_path.chmod(0o755)
        Path(stage, 'portable.flag').touch()
        Path(stage, 'Read me.txt').write_text(
            'Library Studio '+APP_VERSION+'\nFor Jellyfin movies and Jellyfin / Subsonic music libraries.\n\n' +
            (platform.system()+'; Python is bundled. This build runs on the same OS and architecture.\n' if IS_FROZEN else
             'Windows / macOS / Linux; Python 3.10+ with Tcl/Tk is required. No pip packages are required.\n') +
            ('Run LibraryStudio'+('.exe' if os.name == 'nt' else '')+'.\n' if IS_FROZEN else
             'Double-click LibraryStudio.py or Start Library Studio.cmd.\n') + 'Use Tool setup to locate compatible HandBrakeCLI / FFmpeg. Automatic downloads: Windows x64 only.\n'
            'Keep the entire folder together when moving it. No administrator rights or registry changes.\n'
            'WAV ripping works offline without FFmpeg. MP3 uses FFmpeg; movies use HandBrakeCLI.\n'
            'Online metadata needs an Internet connection; you can enter it manually.\n'
            'Audio CD ripping is available on Windows only. Secure-rip verification is not included.\n'
            'These are organized local files: add their output folders to your media server libraries.\n'
            'Third-party tools retain their own licenses; see downloaded license files and source.json.\n'
            'MusicBrainz commercial service terms: https://metabrainz.org/supporters/account-type\n', encoding='utf-8')
        os.rename(stage, target)
    return target



PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")
DEFAULT_CLI_PATH = find_tool("HandBrakeCLI")


class MusicCDWindow(tk.Frame):
    def destroy(self):
        cancel_widget_timers(self)
        super().destroy()

    def __init__(self, parent):
        super().__init__(parent.page_host)
        self.configure(bg=parent.COLOR_BG)
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.toc_data = None
        self.scanned_drive = None
        self.matches = []
        self.tracks = []
        self.controls = []
        self.parent = parent
        self.reduce_motion = parent.reduce_motion
        header = ttk.Frame(self, padding=(24, 20), style='Chrome.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text='Music CD', style='ChromeTitle.TLabel').pack(anchor='w')
        ttk.Label(header, text=('Scan your disc, review the album, and save it to your library.' if os.name == 'nt' else 'Audio CD ripping is currently available on Windows. Movie conversion works here.'), style='Chrome.TLabel').pack(anchor='w', pady=4)
        self.music_footer = ttk.Frame(self, padding=(24, 12))
        self.music_footer.pack(side='bottom', fill='x')
        split = ttk.Panedwindow(self, orient='vertical')
        split.pack(fill='both', expand=True, padx=18)
        self.music_tabs = ttk.Notebook(split)
        split.add(self.music_tabs, weight=3)
        area = ScrollArea(self.music_tabs)
        self.music_tabs.add(area, text='  Disc & output  ')
        card = SoftCard(area.body)
        card.pack(fill='x')
        frame = card.body
        ttk.Label(frame, text='01  Disc & output', style='Section.TLabel').pack(anchor='w', pady=(0, 10))
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 8))
        ttk.Label(row, text='CD drive').pack(side='left', padx=(0, 8))
        self.drive = tk.StringVar()
        self.drive_box = ttk.Combobox(row, textvariable=self.drive, width=8, state='readonly')
        self.drive_box.pack(side='left')
        self.controls.append((self.drive_box, 'readonly'))
        self.button(row, 'Refresh', self.refresh).pack(side='left', padx=6)
        self.scan_button = self.button(row, 'Scan disc', self.scan)
        self.scan_button.configure(style='Accent.TButton')
        self.scan_button.pack(side='left')
        self.format = tk.StringVar(value='WAV')
        self.bitrate = tk.StringVar(value='320')
        for label, var, values in [('Format', self.format, ['WAV', 'MP3']),
                                   ('MP3 kbps', self.bitrate, ['128', '192', '256', '320'])]:
            ttk.Label(row, text=label).pack(side='left', padx=(16, 6))
            box = ttk.Combobox(row, textvariable=var, values=values, width=6, state='readonly')
            box.pack(side='left')
            self.controls.append((box, 'readonly'))
        ttk.Label(frame, text='WAV preserves CD audio. MP3 uses less space.', style='Dim.TLabel').pack(anchor='w', pady=(0, 8))
        self.destination = self.entry(frame, 'Save music to', parent.settings.get('music_destination', os.path.join(os.path.expanduser('~'), 'Music')), self.browse_dest)
        advanced = Foldout(frame, 'Encoder & metadata options')
        advanced.pack(fill='x', pady=6)
        self.ffmpeg = self.entry(advanced.body, 'FFmpeg (MP3 only)', find_tool('ffmpeg'), self.browse_ffmpeg)
        self.online_metadata = tk.BooleanVar(value=parent.settings.get('music_online', True))
        lookup = ttk.Checkbutton(advanced.body, text='Look up album and track details on MusicBrainz', variable=self.online_metadata)
        lookup.pack(anchor='w', pady=6)
        self.controls.append((lookup, 'normal'))
        ttk.Label(advanced.body, text='WAV: 16-bit / 44.1 kHz stereo PCM. MP3 carries ID3 tags; WAV tag support varies by server.', wraplength=740, style='Dim.TLabel').pack(anchor='w')
        album_area = ScrollArea(self.music_tabs)
        self.music_tabs.add(album_area, text='  Album details  ')
        card = SoftCard(album_area.body)
        card.pack(fill='x', pady=(4, 0))
        frame = card.body
        ttk.Label(frame, text='02  Album details', style='Section.TLabel').pack(anchor='w', pady=(0, 8))
        ttk.Label(frame, text='Matching release', style='Dim.TLabel').pack(anchor='w')
        self.release = ttk.Combobox(frame, state='readonly')
        self.release.set('Scan a CD to find releases')
        self.release.pack(fill='x', pady=(4, 10))
        self.release.bind('<<ComboboxSelected>>', self.apply_release)
        self.controls.append((self.release, 'readonly'))
        metadata = ttk.Frame(frame)
        metadata.pack(fill='x')
        metadata.columnconfigure(0, weight=1)
        metadata.columnconfigure(1, weight=1)
        for index, (attr, label, value) in enumerate([('album', 'Album', 'Unknown Album'),
                       ('artist', 'Album artist', 'Unknown Artist'), ('date', 'Date / year', ''), ('disc', 'Disc number', '1')]):
            field = ttk.Frame(metadata, padding=(0, 0, 12, 8))
            field.grid(row=index//2, column=index%2, sticky='ew')
            ttk.Label(field, text=label, style='Dim.TLabel').pack(anchor='w')
            var = tk.StringVar(value=value)
            setattr(self, attr, var)
            entry = ttk.Entry(field, textvariable=var)
            entry.pack(fill='x', pady=(4, 0))
            self.controls.append((entry, 'normal'))
        track_panel = ttk.Frame(split, padding=16)
        split.add(track_panel, weight=2)
        self.after(150, lambda: split.sashpos(0, int(split.winfo_height() * .60)))
        ttk.Label(track_panel, text='03  Review tracks', style='Section.TLabel').pack(anchor='w')
        ttk.Label(track_panel, text='Double-click to edit title and artist. All listed tracks will be ripped.', style='Dim.TLabel').pack(anchor='w', pady=(4, 8))
        table_frame = ttk.Frame(track_panel)
        table_frame.pack(fill='both', expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=('number', 'title', 'artist', 'length'), show='headings', height=4)
        for col, label, width in [('number', '#', 55), ('title', 'Track title', 330), ('artist', 'Artist', 230), ('length', 'Length', 75)]:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, minwidth=55)
        self.table.grid(row=0, column=0, sticky='nsew')
        ybar = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        ybar.grid(row=0, column=1, sticky='ns')
        xbar = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        xbar.grid(row=1, column=0, sticky='ew')
        self.table.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.table.bind('<Double-1>', self.edit_track)
        self.progress = ttk.Progressbar(self.music_footer, maximum=100)
        self.progress.pack(fill='x', pady=(0, 10))
        self.status = tk.StringVar(value='Insert an audio CD, then choose Scan disc.')
        status_label = ttk.Label(self.music_footer, textvariable=self.status, wraplength=820)
        status_label.pack(anchor='w')
        self.music_footer.bind('<Configure>', lambda e: status_label.configure(wraplength=max(200, e.width-48)))
        actions = ttk.Frame(self.music_footer)
        actions.pack(fill='x', pady=(10, 0))
        self.rip_button = self.button(actions, 'Rip CD  →', self.rip)
        self.rip_button.configure(style='Success.TButton')
        self.rip_button.pack(side='right')
        self.cancel_button = ttk.Button(actions, text='Cancel', style='Danger.TButton', command=self.stop.set, state='disabled')
        self.cancel_button.pack(side='right', padx=8)
        ttk.Label(actions, text='Track metadata also saved as JSON.', style='Dim.TLabel').pack(side='left')
        self.refresh()
        self.after(100, self.poll)

    def button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command)
        self.controls.append((widget, 'normal'))
        return widget

    def entry(self, parent, label, value, browse=None):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=3)
        ttk.Label(row, text=label, width=23).pack(side='left')
        var = tk.StringVar(value=value)
        widget = ttk.Entry(row, textvariable=var)
        widget.pack(side='left', fill='x', expand=True)
        self.controls.append((widget, 'normal'))
        if browse:
            self.button(row, 'Browse…', browse).pack(side='left', padx=4)
        return var

    def browse_dest(self):
        path = filedialog.askdirectory(parent=self)
        if path:
            self.destination.set(path)

    def browse_ffmpeg(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[('FFmpeg', 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'), ('All files', '*')])
        if path:
            self.ffmpeg.set(path)

    def refresh(self):
        drives = [f'{chr(n)}:' for n in range(65, 91)
                  if ctypes.windll.kernel32.GetDriveTypeW(f'{chr(n)}:\\') == 5] if os.name == 'nt' else []
        self.drive_box.configure(values=drives)
        if self.drive.get() not in drives:
            self.drive.set(drives[0] if drives else '')

    def set_busy(self, value):
        self.busy = value
        for widget, state in self.controls:
            widget.configure(state='disabled' if value else state)
        self.cancel_button.configure(state='normal' if value else 'disabled')

    def scan(self):
        if os.name != 'nt':
            messagebox.showinfo('Audio CD', 'Audio CD ripping currently requires Windows. You can still convert movies on this system.', parent=self)
            return
        drive = self.drive.get()
        if not drive:
            messagebox.showerror('No CD drive', 'Connect a CD drive and click Refresh drives.', parent=self)
            return
        self.toc_data = None
        self.tracks = []
        self.matches = []
        self.release.set('')
        self.release.configure(values=[])
        self.render_tracks()
        self.stop.clear()
        self.set_busy(True)
        self.status.set('Reading CD and looking up MusicBrainz metadata…')
        threading.Thread(target=self.scan_worker, args=(drive, self.online_metadata.get()), daemon=True).start()

    def scan_worker(self, drive, online=True):
        try:
            with AudioCD(drive) as cd:
                toc = cd.toc()
            warning = ''
            try:
                matches = cd_lookup(toc) if online else []
            except Exception as error:
                matches = []
                warning = f'Metadata lookup failed: {error}. Enter details manually.'
            if not self.stop.is_set():
                self.events.put(('scan', (drive, toc, matches, warning)))
            else:
                self.events.put(('status', 'Scan cancelled.'))
        except Exception as error:
            self.events.put(('status', f'Cannot read CD: {error}'))
        finally:
            self.events.put(('done', None))

    def apply_release(self, event=None):
        if not self.matches:
            return
        match = self.matches[self.release.current()]
        for var, key in [(self.album, 'album'), (self.artist, 'artist'), (self.date, 'date'), (self.disc, 'disc')]:
            var.set(match[key])
        self.tracks = [dict(t, artist=t['artist'] or match['artist']) for t in match['tracks']]
        self.render_tracks()

    def render_tracks(self):
        self.table.delete(*self.table.get_children())
        for i, track in enumerate(self.tracks):
            seconds = (self.toc_data['offsets'][i + 1] - self.toc_data['offsets'][i]) // 75
            self.table.insert('', 'end', iid=str(i), values=(i + self.toc_data['first'], track['title'],
                                                           track['artist'], f'{seconds // 60}:{seconds % 60:02}'))

    def edit_track(self, event):
        if self.busy:
            return
        item = self.table.identify_row(event.y)
        if not item:
            return
        track = self.tracks[int(item)]
        for key in ('title', 'artist'):
            value = simpledialog.askstring('Edit track', key.title(), initialvalue=track[key], parent=self)
            if value is not None:
                track[key] = value.strip()
        self.render_tracks()

    def rip(self):
        if not self.toc_data or self.drive.get() != self.scanned_drive:
            messagebox.showerror('Scan CD first', 'Scan the selected drive before ripping.', parent=self)
            return
        executable = shutil.which(self.ffmpeg.get().strip())
        if self.format.get() == 'MP3' and not executable:
            messagebox.showerror('FFmpeg required', 'Choose ffmpeg.exe to encode MP3 files.', parent=self)
            return
        if not self.destination.get().strip() or not self.disc.get().isdigit() or int(self.disc.get()) < 1:
            messagebox.showerror('Check settings', 'Choose a destination and a positive disc number.', parent=self)
            return
        settings = {key: var.get().strip() for key, var in [('album', self.album), ('artist', self.artist),
                    ('date', self.date), ('disc', self.disc), ('destination', self.destination),
                    ('format', self.format), ('bitrate', self.bitrate)]}
        settings['ffmpeg'] = executable
        self.parent.settings.update({'music_destination': settings['destination'], 'music_online': self.online_metadata.get(), 'ffmpeg': self.ffmpeg.get().strip()})
        try:
            write_json_atomic(SETTINGS_FILE, self.parent.settings)
        except OSError as error:
            messagebox.showerror('Cannot save settings', str(error), parent=self)
            return
        tracks = [dict(t) for t in self.tracks]
        self.stop.clear()
        self.set_busy(True)
        self.parent.animate_progress(self.progress, 0, immediate=True)
        threading.Thread(target=self.rip_worker, args=(settings, tracks, self.toc_data, self.scanned_drive), daemon=True).start()

    def rip_worker(self, settings, tracks, toc, drive):
        completed = skipped = 0
        try:
            target = os.path.join(settings['destination'], cd_safe_name(settings['artist']),
                                  cd_safe_name(settings['album']), 'Disc ' + settings['disc'])
            os.makedirs(target, exist_ok=True)
            with AudioCD(drive) as cd:
                if cd.toc() != toc:
                    raise ValueError('The disc changed. Scan it again before ripping.')
                # Prevent normal eject operations while reading.
                cd.ioctl(0x24804, b'\1', 0)
                try:
                    for i, track in enumerate(tracks):
                        if self.stop.is_set():
                            raise InterruptedError('Ripping cancelled.')
                        number = i + toc['first']
                        title = track['title'] or f'Track {number:02}'
                        dst = os.path.join(target, f'{number:02} - {cd_safe_name(title)}.' + settings['format'].lower())
                        if os.path.exists(dst):
                            skipped += 1
                            continue
                        metadata = {'title': title, 'artist': track['artist'] or settings['artist'],
                                    'album_artist': settings['artist'], 'album': settings['album'],
                                    'date': settings['date'], 'disc': settings['disc'],
                                    'track': f'{number}/{len(tracks)}', 'musicbrainz_discid': cd_disc_id(toc)}
                        self.events.put(('status', f'Ripping track {i + 1}/{len(tracks)}: {title}'))
                        with tempfile.TemporaryDirectory(prefix='.cd-rip-', dir=target) as scratch:
                            wav_path = os.path.join(scratch, 'track.wav')
                            start, end = toc['offsets'][i] - 150, toc['offsets'][i + 1] - 150
                            with wave.open(wav_path, 'wb') as audio:
                                audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                                for sector in range(start, end, 16):
                                    if self.stop.is_set():
                                        raise InterruptedError('Ripping cancelled.')
                                    count = min(16, end - sector)
                                    for attempt in range(3):
                                        try:
                                            data = cd.read(sector, count)
                                            break
                                        except OSError:
                                            if attempt == 2:
                                                raise
                                    audio.writeframesraw(data)
                                    if (sector - start) % 752 == 0:
                                        self.events.put(('progress', 100 * (i + (sector - start) / (end - start)) / len(tracks)))
                            output = wav_path
                            if settings['format'] == 'WAV':
                                cd_tag_wav(wav_path, metadata)
                            else:
                                output = os.path.join(scratch, 'track.mp3')
                                command = [settings['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-nostdin',
                                           '-i', wav_path, '-c:a', 'libmp3lame', '-b:a', settings['bitrate'] + 'k',
                                           '-id3v2_version', '3']
                                for key, value in metadata.items():
                                    command += ['-metadata', f'{key}={value}']
                                command.append(output)
                                with tempfile.TemporaryFile() as errors:
                                    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors,
                                                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                                    while process.poll() is None:
                                        if self.stop.wait(0.1):
                                            process.terminate()
                                            process.wait()
                                            raise InterruptedError('Ripping cancelled.')
                                    if process.returncode:
                                        errors.seek(0)
                                        raise RuntimeError(errors.read().decode('utf-8', 'replace')[-2000:])
                            if self.stop.is_set():
                                raise InterruptedError('Ripping cancelled.')
                            # Windows rename refuses to overwrite an existing file.
                            os.rename(output, dst)
                            completed += 1
                            sidecar = dst + '.metadata.json'
                            if not os.path.exists(sidecar):
                                with open(sidecar, 'x', encoding='utf-8') as file:
                                    json.dump(metadata, file, ensure_ascii=False, indent=2)
                        self.events.put(('progress', 100 * (i + 1) / len(tracks)))
                finally:
                    try:
                        cd.ioctl(0x24804, b'\0', 0)
                    except OSError:
                        pass
            self.events.put(('progress', 100))
            self.events.put(('status', f'Done: {completed} saved, {skipped} existing files skipped. {target}'))
        except Exception as error:
            self.events.put(('status', f'{error} ({completed} tracks saved; {skipped} skipped.)'))
        finally:
            self.events.put(('done', None))

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'status':
                self.status.set(value)
            elif kind == 'progress':
                self.parent.animate_progress(self.progress, value)
            elif kind == 'done':
                self.set_busy(False)
            elif kind == 'scan':
                self.music_tabs.select(1)
                self.scanned_drive, self.toc_data, self.matches, warning = value
                self.album.set('Unknown Album')
                self.artist.set('Unknown Artist')
                self.date.set('')
                self.disc.set('1')
                self.tracks = [{'title': f'Track {n:02}', 'artist': ''}
                               for n in range(self.toc_data['first'], self.toc_data['last'] + 1)]
                self.release.configure(values=[f"{m['artist']} — {m['album']} ({m['date']}, {m['country']}) [Disc {m['disc']}] {m['id']}"
                                               for m in self.matches])
                if self.matches:
                    self.release.current(0)
                    self.apply_release()
                self.render_tracks()
                self.status.set(warning or (f'{len(self.matches)} matching releases. Review metadata before ripping.'
                                            if self.matches else 'No metadata match. Enter album and track details manually.'))
        self.after(100, self.poll)

    def close(self):
        if self.busy:
            self.stop.set()
            self.status.set('Cancelling… Close this window once the operation stops.')
        else:
            self.parent.show_page('Movies')


# --- Modern Styled GUI Application ---
class TranscodeMovieUI(tk.Tk):
    def destroy(self):
        self.motion.settle()
        cancel_widget_timers(self)
        super().destroy()

    def __init__(self):
        super().__init__()
        self.title(APP_NAME + " · Jellyfin & Subsonic")
        self.app_icon = tk.PhotoImage(width=32, height=32)
        self.app_icon.put('#087f8c', to=(0, 0, 32, 32))
        for left, top, right, bottom in ((7, 6, 11, 26), (14, 10, 18, 26), (21, 6, 25, 26)):
            self.app_icon.put('#ffffff', to=(left, top, right, bottom))
        self.iconphoto(True, self.app_icon)
        self.motion = Motion(self)
        width = min(1380, max(1000, self.winfo_screenwidth()-80))
        height = min(900, max(650, self.winfo_screenheight()-100))
        self.geometry(f'{width}x{height}')
        self.minsize(min(1000, width), min(650, height))

        # Process management & threading
        self.current_process = None
        self.is_running = False
        self.cancel_requested = False
        self.log_queue = queue.Queue()
        self.jobs = []
        self.is_scanning = False
        self.ui_queue = queue.Queue()
        self.queue_settings = None
        self.settings = read_settings()
        self.theme_mode = self.settings.get('theme', 'light')
        if self.theme_mode not in ('light', 'dark'):
            self.theme_mode = 'light'
        set_palette(self.theme_mode)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        # Load presets
        self.presets = self.load_presets()

        # Build theme and UI widgets
        self.setup_theme()
        self.create_widgets()
        self.load_preset_values("Automatic (recommended)")
        if self.settings.get("movie_destination"):
            self.dst_entry.delete(0, "end")
            self.dst_entry.insert(0, self.settings["movie_destination"])

        # Start log consumer
        self.bind('<Control-o>', lambda e: self.browse_source_file())
        self.bind('<Control-comma>', lambda e: self.open_settings())
        self.bind('<Escape>', lambda e: self.show_page('Movies'))
        self.after(100, self.process_log_queue)
        # Automated page checks provide their own fixtures and intentionally need no tools.
        # A delayed first-run redirect must not interrupt those navigation checks.
        if '--self-test-report' not in sys.argv:
            self.after(250, self.first_run_hint)
        self.compact_movies = None
        self.bind('<Configure>', self.resize_movies, add='+')
        self.after_idle(self.resize_movies)

    def open_music_cd(self):
        self.show_page('Music CD')

    def show_page(self, name):
        # Pages stay alive while hidden so background jobs and unsaved inputs survive navigation.
        if name == 'Music CD' and name not in self.pages:
            self.music_window = MusicCDWindow(self)
            self.music_window.grid(row=0, column=0, sticky='nsew')
            self.pages[name] = self.music_window
        elif name == 'Tool setup' and name not in self.pages:
            self.setup_window = SetupWindow(self)
            self.setup_window.grid(row=0, column=0, sticky='nsew')
            self.pages[name] = self.setup_window
        if name not in self.pages:
            return
        self.update_preset_summary()
        previous = self.active_page
        if previous != name:
            self.animate_panel(self.pages[name], 'page', 1 if ('Movies', 'Music CD', 'Settings', 'Tool setup').index(name) > ('Movies', 'Music CD', 'Settings', 'Tool setup').index(previous) else -1)
        else:
            self.pages[name].tkraise()
        self.active_page = name
        self.title(APP_NAME + ' · ' + name)
        for label, button in self.page_nav.items():
            selected = label == name or (label == 'Activity' and name == 'Settings' and self.active_settings_section == 'Activity')
            if label == 'Settings' and self.active_settings_section == 'Activity' and name == 'Settings':
                selected = False
            self.motion.cancel('hover:' + str(button))
            button.motion_base_style = 'Selected.Nav.TButton' if selected else 'Nav.TButton'
            button.configure(style=button.motion_base_style)
            if selected:
                self.move_navigation_marker(button)

    def hover_navigation(self, button, entering):
        style = ttk.Style(self)
        base = getattr(button, 'motion_base_style', 'Nav.TButton')
        start_color = style.lookup(button.cget('style'), 'foreground') or INK
        end_color = ACCENT if entering or base == 'Selected.Nav.TButton' else INK
        start = tuple(int(start_color[i:i+2], 16) for i in (1,3,5))
        end = tuple(int(end_color[i:i+2], 16) for i in (1,3,5))
        name = 'Hover' + str(button).replace('.', '_') + '.' + base
        button.configure(style=name)
        def draw(t):
            rgb = tuple(round(a+(b-a)*t) for a,b in zip(start,end))
            style.configure(name, foreground='#%02x%02x%02x' % rgb)
        def finish():
            draw(1)
            if not entering:
                button.configure(style=base)
        self.motion.tween('hover:' + str(button), 0, 1, draw, duration=.16, finish=finish)

    def animate_panel(self, panel, key, direction=1):
        self.motion.cancel(key)
        panel.tkraise()
        if self.motion.reduced() or not self.winfo_ismapped():
            return
        panel.place(x=28*direction, y=0, relwidth=1, relheight=1)
        panel.tkraise()
        def finish():
            panel.place_forget()
            panel.grid(row=0, column=0, sticky='nsew')
            panel.tkraise()
        self.motion.tween(key, 28*direction, 0, lambda x: panel.place_configure(x=round(x)), duration=.30, finish=finish)

    def move_navigation_marker(self, button):
        self.update_idletasks()
        target = button.winfo_y()+5
        start = self.nav_marker.winfo_y() if self.nav_marker.winfo_ismapped() else target
        self.nav_marker.configure(bg=ACCENT)
        self.nav_marker.place(x=0, y=start, width=3, height=max(20,button.winfo_height()-10))
        self.nav_marker.lift()
        self.motion.tween('navigation', start, target, lambda y: self.nav_marker.place_configure(y=round(y)),
                          finish=lambda: self.nav_marker.place_configure(y=target))

    def animate_progress(self, bar, value, immediate=False):
        value = max(0., min(100., float(value)))
        key = 'progress:' + str(bar)
        start = float(bar.cget('value'))
        if immediate or value < start:
            self.motion.cancel(key)
            bar.configure(value=value)
            return
        self.motion.tween(key, start, value, lambda v: bar.configure(value=v), duration=.18,
                          finish=lambda: bar.configure(value=value), ease=lambda t: 1-(1-t)**3)

    def setup_theme(self):
        self.COLOR_BG = BG
        self.COLOR_PANEL = self.COLOR_CARD = COLORS['surface']
        self.COLOR_ACCENT = ACCENT
        self.COLOR_ACCENT_HOVER = COLORS['accent_hover']
        self.COLOR_SUCCESS = COLORS['success']
        self.COLOR_DANGER = COLORS['danger']
        self.COLOR_TEXT = INK
        self.COLOR_TEXT_DIM = MUTED
        self.COLOR_BORDER = COLORS['border']
        self.COLOR_ENTRY = COLORS['entry']
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background=COLORS['surface'], foreground=INK, font=(UI_FONT, 10))
        for name in ('TFrame', 'Card.TFrame', 'Header.TFrame'):
            style.configure(name, background=COLORS['surface'])
        style.configure('TLabel', background=COLORS['surface'], foreground=INK)
        style.configure('Chrome.TFrame', background=BG)
        style.configure('Chrome.TLabel', background=BG, foreground=MUTED)
        style.configure('ChromeTitle.TLabel', background=BG, foreground=INK, font=(UI_BOLD, 30))
        style.configure('CompactTitle.TLabel', background=BG, foreground=INK, font=(UI_BOLD, 22))
        style.configure('Brand.TLabel', background=BG, foreground=INK, font=(UI_BOLD, 16))
        style.configure('Eyebrow.TLabel', background=BG, foreground=MUTED, font=(UI_BOLD, 9))
        style.configure('Empty.TLabel', background=COLORS['entry'], foreground=MUTED, font=(UI_FONT, 11))
        style.configure('Dim.TLabel', foreground=MUTED, font=(UI_FONT, 9))
        style.configure('Section.TLabel', foreground=INK, font=(UI_BOLD, 12))
        style.configure('Title.TLabel', foreground=INK, font=(UI_BOLD, 24))
        style.configure('Subtitle.TLabel', foreground=MUTED, font=(UI_FONT, 10))
        style.configure('TButton', padding=(12, 2), background=COLORS['surface'], bordercolor=COLORS['border'],
                        lightcolor=COLORS['highlight'], darkcolor=COLORS['shadow'], relief='flat', borderwidth=0)
        style.map('TButton', background=[('active', COLORS['hover']), ('pressed', COLORS['pressed'])],
                  foreground=[('disabled', COLORS['disabled']), ('focus', ACCENT)], relief=[('pressed', 'flat')])
        style.configure('Accent.TButton', background=ACCENT, foreground='white', font=(UI_BOLD, 10))
        style.map('Accent.TButton', background=[('active', COLORS['accent_hover']), ('disabled', COLORS['border'])], foreground=[('disabled', COLORS['muted'])])
        style.configure('Success.TButton', background=ACCENT, foreground='white', padding=(18, 3))
        style.map('Success.TButton', background=[('active', COLORS['accent_hover']), ('disabled', COLORS['border'])])
        style.configure('Danger.TButton', foreground=COLORS['danger'])
        style.configure('Fold.TButton', anchor='w', padding=(12, 6), font=(UI_BOLD, 10))
        style.configure('TEntry', fieldbackground=COLORS['entry'], foreground=INK, padding=9,
                        bordercolor=COLORS['border'], lightcolor=COLORS['border'], darkcolor=COLORS['highlight'])
        style.configure('TCombobox', fieldbackground=COLORS['entry'], foreground=INK, padding=8, arrowsize=14)
        style.map('TCombobox', fieldbackground=[('readonly', COLORS['entry'])], selectbackground=[('readonly', COLORS['selection'])],
                  selectforeground=[('readonly', INK)])
        style.configure('Treeview', background=COLORS['entry'], fieldbackground=COLORS['entry'], foreground=INK,
                        rowheight=38, borderwidth=0, font=(UI_FONT, 10))
        style.configure('Treeview.Heading', background=COLORS['heading'], foreground=MUTED, padding=7, font=(UI_BOLD, 9))
        style.map('Treeview', background=[('selected', COLORS['selection'])], foreground=[('selected', INK)])
        style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=COLORS['border'], borderwidth=0, thickness=5)
        style.configure('TCheckbutton', background=COLORS['surface'], foreground=INK)
        style.map('TCheckbutton', background=[('active', COLORS['surface'])], foreground=[('disabled', COLORS['disabled'])])
        style.configure('TSpinbox', fieldbackground=COLORS['entry'], foreground=INK, background=BG,
                        arrowcolor=INK, bordercolor=COLORS['border'])
        style.configure('TCombobox', background=BG, arrowcolor=INK, bordercolor=COLORS['border'])
        style.map('TEntry', fieldbackground=[('disabled', BG)], foreground=[('disabled', COLORS['disabled'])])
        style.map('TCombobox', foreground=[('disabled', COLORS['disabled']), ('readonly', INK)])
        style.configure('TScrollbar', background=COLORS['heading'], troughcolor=BG, arrowcolor=INK,
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        configure_soft_buttons(self, style)
        style.configure('Nav.TButton', anchor='w', padding=(14, 4), font=(UI_FONT, 11))
        style.configure('Selected.Nav.TButton', foreground=ACCENT, font=(UI_BOLD, 11))
        style.configure('TEntry', lightcolor=COLORS['border'], darkcolor=COLORS['border'])
        style.map('TEntry', bordercolor=[('focus', ACCENT)])
        style.map('TCombobox', bordercolor=[('focus', ACCENT)])
        style.configure('Horizontal.TProgressbar', lightcolor=ACCENT, darkcolor=ACCENT,
                        troughcolor=COLORS['border'], bordercolor=BG)
        style.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
            ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        style.layout('Horizontal.TScrollbar', [('Horizontal.Scrollbar.trough', {'sticky': 'we', 'children': [
            ('Horizontal.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        # Remove the stock Clam bevels; let rounded image surfaces define controls.
        for button_style in ('TButton', 'Accent.TButton', 'Success.TButton'):
            style.configure(button_style, background=COLORS['surface'], borderwidth=0)
            style.map(button_style, background=[('active', COLORS['surface']), ('disabled', COLORS['surface'])])
        style.configure('Nav.TButton', background=BG)
        style.configure('Selected.Nav.TButton', background=COLORS['selection'])
        style.configure('Selected.Nav.TButton', padding=(14, 4))
        style.map('Nav.TButton', background=[('active', BG)])
        style.configure('Treeview', bordercolor=COLORS['entry'], lightcolor=COLORS['entry'], darkcolor=COLORS['entry'])
        style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        style.configure('Treeview.Heading', relief='flat', borderwidth=0,
                        bordercolor=COLORS['heading'], lightcolor=COLORS['heading'], darkcolor=COLORS['heading'])
        style.map('Treeview.Heading', background=[('active', COLORS['heading'])], relief=[('active', 'flat')])
        style.configure('TCombobox', lightcolor=COLORS['border'], darkcolor=COLORS['border'], borderwidth=1)
        style.configure('TScrollbar', borderwidth=0, arrowsize=9, relief='flat')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=COLORS['heading'], foreground=MUTED, padding=(14, 9))
        style.map('TNotebook.Tab', background=[('selected', COLORS['surface'])], foreground=[('selected', ACCENT)])
        self.option_add('*TCombobox*Listbox.background', COLORS['entry'])
        self.option_add('*TCombobox*Listbox.foreground', INK)

    def create_widgets(self):
        shell = ttk.Frame(self, padding=(18, 20), style='Chrome.TFrame')
        shell.pack(fill='both', expand=True)
        sidebar = ttk.Frame(shell, width=180, padding=(0, 10, 18, 0), style='Chrome.TFrame')
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text='LIBRARY', style='Eyebrow.TLabel').pack(anchor='w', padx=12)
        ttk.Label(sidebar, text='Studio', style='ChromeTitle.TLabel').pack(anchor='w', padx=10, pady=(0, 30))
        self.nav_marker = tk.Frame(sidebar, bg=ACCENT)
        self.page_nav = {}
        for label, command in [('Movies', self.focus_movies), ('Music CD', self.open_music_cd),
                               ('Settings', self.open_settings), ('Activity', lambda: self.open_settings('Activity')),
                               ('Tool setup', self.open_setup)]:
            if label == 'Settings':
                ttk.Separator(sidebar).pack(fill='x', padx=12, pady=22)
            button = ttk.Button(sidebar, text=label, style='Nav.TButton', command=command)
            button.pack(fill='x', pady=4)
            self.page_nav[label] = button
            button.bind('<Enter>', lambda e, b=button: self.hover_navigation(b, True))
            button.bind('<Leave>', lambda e, b=button: self.hover_navigation(b, False))
        self.background_status = tk.StringVar(value='All tasks idle')
        ttk.Label(sidebar, textvariable=self.background_status, style='Chrome.TLabel', wraplength=150).pack(anchor='w', padx=12, pady=22)
        ttk.Label(sidebar, text='Jellyfin + Subsonic\nLocal media libraries', style='Chrome.TLabel').pack(side='bottom', anchor='w', padx=12, pady=12)
        ttk.Button(sidebar, text='Open library ↗', command=self.open_dest_folder).pack(side='bottom', fill='x')
        self.page_host = ttk.Frame(shell, style='Chrome.TFrame')
        self.page_host.pack(side='left', fill='both', expand=True)
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        main = ttk.Frame(self.page_host, style='Chrome.TFrame')
        main.grid(row=0, column=0, sticky='nsew')
        self.pages = {'Movies': main}
        self.active_page = 'Movies'
        self.active_settings_section = 'Appearance'
        heading = ttk.Frame(main, padding=(12, 0, 12, 16), style='Chrome.TFrame')
        self.movie_heading = heading
        heading.pack(fill='x')
        ttk.Button(heading, text='Naming & metadata', command=lambda: self.open_settings('Naming & metadata')).pack(side='right', anchor='center')
        self.movie_title = ttk.Label(heading, text='Movie workspace', style='ChromeTitle.TLabel')
        self.movie_title.pack(anchor='w')
        self.movie_tagline = ttk.Label(heading, text='Choose your source. Preview your library. Start converting.', style='Chrome.TLabel')
        self.movie_tagline.pack(anchor='w', pady=(4, 0))

        footer = ttk.Frame(main, padding=(14, 12), style='Card.TFrame')
        self.movie_footer = footer
        footer.pack(side='bottom', fill='x', padx=8, pady=(12, 0))
        self.lbl_task_status = ttk.Label(footer, text='Ready · Choose a movie file or folder to begin.', wraplength=720)
        self.lbl_task_status.pack(anchor='w')
        self.lbl_eta = ttk.Label(footer, text='', style='Dim.TLabel', wraplength=720)
        self.lbl_eta.pack(anchor='w', pady=(4, 0))
        self.progress_bar = ttk.Progressbar(footer, maximum=100)
        self.progress_bar.pack(fill='x', pady=(10, 12))
        actions = ttk.Frame(footer)
        actions.pack(fill='x')
        self.btn_start = ttk.Button(actions, text='Start conversion  →', style='Success.TButton', command=self.start_transcode_thread)
        self.btn_start.pack(side='right')
        self.btn_cancel = ttk.Button(actions, text='Cancel', style='Danger.TButton', command=self.cancel_transcoding, state='disabled')
        self.btn_cancel.pack(side='right', padx=8)
        self.lbl_stats = ttk.Label(actions, text='', style='Dim.TLabel', wraplength=400)
        self.lbl_stats.pack(side='left')

        source_card = SoftCard(main)
        self.source_card = source_card
        source_card.pack(fill='x', pady=(0, 6))
        frame = source_card.body
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        locations = ttk.Frame(frame, padding=(0, 0, 20, 0))
        locations.grid(row=0, column=0, sticky='nsew')
        ttk.Label(locations, text='01  Source & destination', style='Section.TLabel').pack(anchor='w', pady=(0, 12))
        ttk.Label(locations, text='Movie file or folder', style='Dim.TLabel').pack(anchor='w')
        row = ttk.Frame(locations)
        row.pack(fill='x', pady=(4, 12))
        self.src_entry = ttk.Entry(row)
        self.src_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='File…', command=self.browse_source_file).pack(side='left', padx=(5, 0))
        ttk.Button(row, text='Folder…', command=self.browse_source_folder).pack(side='left', padx=(5, 0))
        ttk.Label(locations, text='Save movies to', style='Dim.TLabel').pack(anchor='w')
        row = ttk.Frame(locations)
        row.pack(fill='x', pady=(4, 0))
        self.dst_entry = ttk.Entry(row)
        self.dst_entry.insert(0, self.settings.get('movie_destination', DEFAULT_DST_ROOT))
        self.dst_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Browse…', command=self.browse_dest_folder).pack(side='left', padx=(5, 0))
        profile = ttk.Frame(frame, padding=(18, 0, 0, 0))
        profile.grid(row=0, column=1, sticky='nsew')
        ttk.Label(profile, text='02  Output settings', style='Section.TLabel').pack(anchor='w', pady=(0, 8))
        self.preset_combo = tk.StringVar(value='Automatic (recommended)')
        self.output_format_var = tk.StringVar()
        self.output_rate_var = tk.StringVar()
        self.output_value_var = tk.StringVar()
        self.syncing_output = False
        self.output_custom_choice = None
        ttk.Label(profile, text='Format / resolution', style='Dim.TLabel').pack(anchor='w')
        self.output_format_combo = ttk.Combobox(profile, textvariable=self.output_format_var,
                                               values=list(OUTPUT_CHOICES), state='readonly')
        self.output_format_combo.pack(fill='x', pady=(2, 5))
        self.output_format_combo.bind('<<ComboboxSelected>>', self.on_output_format_selected)
        ttk.Label(profile, text='Bitrate / quality', style='Dim.TLabel').pack(anchor='w')
        self.output_rate_combo = ttk.Combobox(profile, textvariable=self.output_rate_var, state='readonly')
        self.output_rate_combo.pack(fill='x', pady=(2, 5))
        self.output_rate_combo.bind('<<ComboboxSelected>>', self.on_output_rate_selected)
        self.output_value_row = ttk.Frame(profile)
        self.output_value_label = ttk.Label(self.output_value_row, style='Dim.TLabel')
        self.output_value_label.pack(side='left', padx=(0, 8))
        self.output_value_entry = ttk.Entry(self.output_value_row, textvariable=self.output_value_var, width=12)
        self.output_value_entry.pack(side='left', fill='x', expand=True)
        self.output_value_var.trace_add('write', self.on_output_value_changed)
        self.preset_summary = tk.StringVar()
        summary = ttk.Label(profile, textvariable=self.preset_summary, style='Dim.TLabel', wraplength=300)
        self.profile_summary_label = summary
        summary.pack(anchor='w', fill='x')
        profile.bind('<Configure>', lambda e: summary.configure(wraplength=max(100, e.width-20)))
        self.profile_actions = ttk.Frame(profile)
        self.profile_actions.pack(fill='x', pady=(6, 0))
        ttk.Button(self.profile_actions, text='Audio & advanced…', command=lambda: self.open_settings('Picture & sound')).pack(side='left')

        queue_panel = ttk.Frame(main, padding=16)
        self.queue_panel = queue_panel
        queue_panel.pack(fill='both', expand=True, padx=8)
        queue_panel.columnconfigure(0, weight=1)
        queue_panel.rowconfigure(1, weight=1)
        row = ttk.Frame(queue_panel)
        row.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(row, text='03  Preview queue', style='Section.TLabel').pack(side='left')
        self.queue_count = tk.StringVar(value='0 files')
        ttk.Label(row, textvariable=self.queue_count, style='Dim.TLabel').pack(side='left', padx=14)
        self.btn_scan = ttk.Button(row, text='Scan & preview', style='Accent.TButton', command=self.start_preview_scan)
        self.btn_scan.pack(side='right')
        self.btn_clear = ttk.Button(row, text='Clear', command=self.clear_queue)
        self.btn_clear.pack(side='right', padx=4)
        self.btn_remove = ttk.Button(row, text='Remove', command=self.remove_selected_item, state='disabled')
        self.btn_remove.pack(side='right', padx=4)
        columns = ('type', 'name', 'size', 'info', 'preset', 'destination')
        table = ttk.Frame(queue_panel)
        table.grid(row=1, column=0, sticky='nsew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=columns, show='headings', height=5, selectmode='browse')
        for col, title, width in zip(columns, ['Type', 'Movie / extra', 'Source size', 'Source media', 'Output profile / encoder', 'Destination'], [85,260,90,160,350,300]):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=150 if col=='name' else 65, stretch=col=='name')
        self.tree.grid(row=0, column=0, sticky='nsew')
        ttk.Scrollbar(table, orient='vertical', command=self.tree.yview).grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=table.grid_slaves(row=0, column=1)[0].set)
        xbar = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        xbar.grid(row=1, column=0, sticky='ew')
        self.tree.configure(xscrollcommand=xbar.set)
        self.empty_queue = ttk.Label(self.tree, text='Ready for your next movie\n\nChoose a file or folder above, then scan to see\nexactly what will be saved to your library.',
                                     style='Empty.TLabel', justify='center', anchor='center')
        self.empty_queue.place(relx=.5, rely=.55, anchor='center')
        self.queue_hint = ttk.Label(queue_panel, text='Source files stay untouched. Existing output files are skipped.', style='Dim.TLabel')
        self.queue_hint.grid(row=2, column=0, sticky='ew', pady=(10, 4))
        self.selected_destination = tk.StringVar(value='Select a queued item to see its full output path.')
        detail = ttk.Label(queue_panel, textvariable=self.selected_destination, style='Dim.TLabel', wraplength=740)
        detail.grid(row=3, column=0, sticky='ew')
        queue_panel.bind('<Configure>', lambda e: detail.configure(wraplength=max(200, e.width-32)))
        self.tree.bind('<Configure>', self.update_empty_queue_layout)
        self.tree.bind('<<TreeviewSelect>>', self.update_queue_selection)
        self.tree.bind('<Delete>', lambda e: self.remove_selected_item())

        self.preferences_window = ttk.Frame(self.page_host, style='Chrome.TFrame')
        self.preferences_window.grid(row=0, column=0, sticky='nsew')
        self.pages['Settings'] = self.preferences_window
        top = ttk.Frame(self.preferences_window, padding=(24, 20))
        top.pack(fill='x')
        ttk.Label(top, text='Settings', style='Title.TLabel').pack(anchor='w')
        ttk.Label(top, text='Fine-tune your library and conversion profiles.', style='Subtitle.TLabel').pack(anchor='w', pady=(5, 0))
        bottom = ttk.Frame(self.preferences_window, padding=(24, 12))
        bottom.pack(side='bottom', fill='x')
        ttk.Label(bottom, text='Changes apply to the next conversion.', style='Dim.TLabel').pack(side='left')
        ttk.Button(bottom, text='Done', style='Accent.TButton', command=self.hide_settings).pack(side='right')
        workspace = ttk.Frame(self.preferences_window, style='Chrome.TFrame', padding=16)
        workspace.pack(fill='both', expand=True)
        nav = ttk.Frame(workspace, width=210, style='Chrome.TFrame', padding=(0, 4, 12, 0))
        nav.pack(side='left', fill='y')
        nav.pack_propagate(False)
        pages = ttk.Frame(workspace, style='Chrome.TFrame')
        pages.pack(side='left', fill='both', expand=True)
        pages.rowconfigure(0, weight=1)
        pages.columnconfigure(0, weight=1)
        self.settings_sections = {}
        self.settings_nav = {}
        def section(title, subtitle):
            area = ScrollArea(pages)
            area.grid(row=0, column=0, sticky='nsew')
            self.settings_sections[title] = area
            button = ttk.Button(nav, text=title, style='Nav.TButton', command=lambda t=title: self.show_settings_section(t))
            button.pack(fill='x', pady=4)
            self.settings_nav[title] = button
            card = SoftCard(area.body)
            card.pack(fill='x')
            ttk.Label(card.body, text=title, style='Section.TLabel').pack(anchor='w')
            ttk.Label(card.body, text=subtitle, style='Dim.TLabel').pack(anchor='w', pady=(4, 16))
            return card.body
        appearance = section('Appearance', 'light, dark and animation')
        self.theme_button = ttk.Button(appearance, text='Light / Dark', command=self.toggle_theme)
        self.theme_button.pack(anchor='w', pady=4)
        self.reduce_motion = tk.BooleanVar(value=self.settings.get('reduce_motion', False))
        self.reduce_motion.trace_add('write', lambda *args: self.motion.settle() if self.reduce_motion.get() else None)
        self.preferences_window.reduce_motion = self.reduce_motion
        ttk.Checkbutton(appearance, text='Reduce animation', variable=self.reduce_motion).pack(anchor='w', pady=6)
        naming = section('Naming & metadata', 'optional title and year')
        self.name_entry = self.path_row(naming, 'Movie title', '')
        self.year_entry = self.path_row(naming, 'Year', '', self.lookup_year_ui, 'Look up year')
        ttk.Label(naming, text='Leave blank to use the folder or filename. Review the detected year before converting.', style='Dim.TLabel').pack(anchor='w', pady=6)
        self.online_year = tk.BooleanVar(value=self.settings.get('online_year', True))
        ttk.Checkbutton(naming, text='Look up missing movie years online', variable=self.online_year).pack(anchor='w')
        encoding = section('Picture & sound', 'automatic by default')
        ttk.Label(encoding, text='Original quality copies the MKV exactly; encoding and audio settings are bypassed.\nOther modes convert to MP4. Use Save as… to save a profile.', style='Dim.TLabel').pack(anchor='w', pady=(0, 6))
        self.engine_var = tk.StringVar(value=self.settings.get('movie_engine', ENGINE_AUTO))
        if self.engine_var.get() not in ENGINES:
            self.engine_var.set(ENGINE_AUTO)
        engine = self.combo_row(encoding, 'Video processing', self.engine_var, ENGINES)
        engine.bind('<<ComboboxSelected>>', lambda e: self.update_preset_summary())
        ttk.Label(encoding, text='Automatic checks this computer for NVIDIA, AMD, Intel or Apple encoders, then uses CPU if needed.\nAudio and filters still use CPU. Original Quality copies the file without encoding.', style='Dim.TLabel', wraplength=700).pack(anchor='w', pady=5)
        self.fallback_var = tk.BooleanVar(value=self.settings.get('encoder_fallback', True))
        self.responsive_var = tk.BooleanVar(value=self.settings.get('responsive_cpu', True))
        ttk.Checkbutton(encoding, text='Retry with CPU if the GPU cannot start encoding', variable=self.fallback_var).pack(anchor='w', pady=3)
        ttk.Checkbutton(encoding, text='Keep computer responsive during CPU encoding', variable=self.responsive_var).pack(anchor='w', pady=3)
        self.encoder_status = tk.StringVar(value='Encoder will be checked when you scan. No GPU required.')
        ttk.Label(encoding, textvariable=self.encoder_status, style='Dim.TLabel', wraplength=650).pack(anchor='w', pady=8)
        ttk.Button(encoding, text='Check available encoders', command=self.check_encoders_ui).pack(anchor='w', pady=(0, 12))
        self.saved_profile_combo = ttk.Combobox(encoding, values=[*[name for name in self.presets if name not in DEFAULT_PRESETS], CUSTOM_PRESET], state='readonly')
        self.saved_profile_combo.set('Select a saved profile…')
        ttk.Label(encoding, text='Saved profiles', style='Dim.TLabel').pack(anchor='w', pady=(8, 3))
        self.saved_profile_combo.pack(fill='x', pady=(0, 10))
        self.saved_profile_combo.bind('<<ComboboxSelected>>', self.on_saved_profile_selected)
        self.core_preset_var = tk.StringVar(value='auto')
        core = self.combo_row(encoding, 'HandBrake preset', self.core_preset_var,
                       ['auto', *OFFICIAL_PRESETS], editable=True)
        core.bind('<<ComboboxSelected>>', lambda event: self.update_preset_summary())
        core.bind('<FocusOut>', lambda event: self.update_preset_summary())
        ttk.Label(encoding, text='720p / 1080p use H.264. 4K uses 10-bit HEVC (H.265). Smaller sources are not upscaled.\nTarget bitrates do not guarantee an exact file size. Original Quality preserves the source without loss.', style='Dim.TLabel', wraplength=700).pack(anchor='w', pady=5)
        self.video_mode_var = tk.StringVar(value='Use HandBrake Preset Bitrate/RF (Recommended)')
        video = self.combo_row(encoding, 'Picture quality', self.video_mode_var,
                       [AUTO_VIDEO_MODE, 'Use HandBrake Preset Bitrate/RF (Recommended)', 'Custom Target Bitrate (kbps)', 'Custom Constant Quality (RF / CQ)', 'Original quality (copy source MKV)'])
        video.bind('<<ComboboxSelected>>', self.on_video_mode_change)
        self.video_bitrate_entry = self.path_row(encoding, 'Bitrate (kbps)', '4000')
        self.video_rf_entry = self.path_row(encoding, 'Quality (RF)', '22')
        self.video_bitrate_entry.bind('<KeyRelease>', lambda event: self.update_preset_summary())
        self.video_rf_entry.bind('<KeyRelease>', lambda event: self.update_preset_summary())
        self.audio_mode_var = tk.StringVar(value='Smart Surround (Stereo AAC + 5.1 AC3 if present)')
        self.combo_row(encoding, 'Sound', self.audio_mode_var, ['Smart Surround (Stereo AAC + 5.1 AC3 if present)',
            'Stereo Only (Downmix first track)', 'Preset Audio Default', 'Passthrough (Original Audio Streams)'])
        self.stereo_kbps_combo = self.combo_row(encoding, 'Stereo AAC kbps', tk.StringVar(value='192'), ['128','160','192','256','320'])
        self.surround_kbps_combo = self.combo_row(encoding, 'Surround AC3 kbps', tk.StringVar(value='512'), ['384','448','512','640'])
        profile_actions = ttk.Frame(encoding)
        profile_actions.pack(fill='x', pady=8)
        ttk.Button(profile_actions, text='Save as custom profile…', command=self.save_current_preset).pack(side='left')
        ttk.Button(profile_actions, text='Delete selected profile', command=self.delete_current_preset).pack(side='left', padx=6)
        extras = section('Bonus features', 'Jellyfin extras organization')
        self.extras_mode_var = tk.StringVar(value='Subfolder: extras / numbered')
        self.combo_row(extras, 'Save extras', self.extras_mode_var, ['Subfolder: extras / numbered',
            'Subfolder: extras / Original filenames', 'Same Folder: numbered featurettes', 'Skip Extras (main movie only)'])
        row = ttk.Frame(extras)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='Minimum size (MB)', width=22).pack(side='left')
        self.min_size_var = tk.IntVar(value=10)
        self.min_size_spin = ttk.Spinbox(row, from_=0, to=10000, textvariable=self.min_size_var, width=8)
        self.min_size_spin.pack(side='left')
        self.dedup_extras_var = tk.BooleanVar(value=True)
        self.include_subfolder_extras_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(extras, text='Ignore exact duplicate files', variable=self.dedup_extras_var).pack(anchor='w', pady=3)
        ttk.Checkbutton(extras, text='Include an existing Extras subfolder', variable=self.include_subfolder_extras_var).pack(anchor='w', pady=3)
        tools = section('Tools & storage', 'portable paths and network storage')
        self.cli_entry = self.path_row(tools, 'HandBrakeCLI', find_tool('HandBrakeCLI'), self.browse_cli_file)
        self.remote_scratch_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(tools, text='Encode locally before copying to a network destination', variable=self.remote_scratch_var).pack(anchor='w', pady=6)
        ttk.Button(tools, text='Open output folder', command=self.open_dest_folder).pack(anchor='w', pady=4)
        ttk.Button(tools, text='Check tools / set up portable copy', command=self.open_setup).pack(anchor='w', pady=8)
        activity = section('Activity', 'progress and troubleshooting')
        self.log_text = tk.Text(activity, height=7, bg=COLORS['entry'], fg=INK, font=(MONO_FONT, 9), relief='flat', wrap='word')
        self.log_text.pack(fill='x')
        for tag, color in [('SUCCESS', COLORS['success']), ('ERROR', COLORS['danger']), ('INFO', ACCENT), ('WARNING', COLORS['warning']), ('DIM', MUTED)]:
            self.log_text.tag_config(tag, foreground=color)
        ttk.Button(activity, text='Save activity log…', command=self.export_activity).pack(anchor='e', pady=4)
        ttk.Label(activity, text='Logs include local file paths. Review them before sharing a bug report.', style='Dim.TLabel', wraplength=650).pack(anchor='w', pady=4)
        ttk.Button(activity, text='Clear activity', command=self.clear_log).pack(anchor='e', pady=4)

        self.show_settings_section('Appearance')
        self.show_page('Movies')

    def resize_movies(self, event=None):
        if event is not None and event.widget is not self:
            return
        compact = self.winfo_height() < 780
        if compact == self.compact_movies:
            return
        self.compact_movies = compact
        self.movie_heading.configure(padding=(12, 0, 12, 4 if compact else 16))
        self.movie_title.configure(style='CompactTitle.TLabel' if compact else 'ChromeTitle.TLabel')
        if compact:
            self.movie_tagline.pack_forget()
            self.queue_hint.grid_remove()
            self.profile_actions.pack_forget()
        else:
            self.movie_tagline.pack(anchor='w', pady=(4, 0))
            self.queue_hint.grid()
            self.profile_actions.pack(fill='x', pady=(6, 0))
        self.source_card.body.configure(padding=(18, 6 if compact else 16))
        self.source_card.inset = 8 if compact else 15
        self.source_card.coords(self.source_card.window, 17, self.source_card.inset)
        self.source_card.fit()
        self.movie_footer.configure(padding=(14, 4 if compact else 12))
        self.lbl_eta.pack_configure(pady=(0, 0) if compact else (4, 0))
        self.movie_footer.pack_configure(pady=(6 if compact else 12, 0))
        self.progress_bar.pack_configure(pady=(4, 6) if compact else (10, 12))
        self.queue_panel.configure(padding=(12, 6) if compact else 16)

    def update_empty_queue_layout(self, event=None):
        compact = self.tree.winfo_height() < 170
        self.empty_queue.configure(text='Choose a source, then scan to preview your movies.' if compact else
            'Ready for your next movie\n\nChoose a file or folder above, then scan to see\nexactly what will be saved to your library.',
            wraplength=max(200, self.tree.winfo_width()-60))
        self.empty_queue.place_configure(rely=.62 if compact else .55)

    def focus_movies(self):
        self.show_page('Movies')
        self.src_entry.focus_set()

    def show_settings_section(self, title):
        changed = self.active_settings_section != title
        self.active_settings_section = title
        if changed and self.active_page == 'Settings':
            self.animate_panel(self.settings_sections[title], 'settings-page')
        else:
            self.settings_sections[title].tkraise()
        if self.active_page == 'Settings':
            self.show_page('Settings')
        for name, button in self.settings_nav.items():
            button.configure(style='Selected.Nav.TButton' if name == title else 'Nav.TButton')

    def update_queue_selection(self, event=None):
        selected = self.tree.selection()
        self.btn_remove.configure(state='normal' if selected and not (self.is_running or self.is_scanning) else 'disabled')
        if selected:
            self.selected_destination.set('Output: ' + str(self.tree.item(selected[0], 'values')[-1]))
        else:
            self.selected_destination.set('Select a queued item to see its full output path.')

    def path_row(self, parent, title, value, command=None, button='Browse…'):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=4)
        ttk.Label(row, text=title, width=18).pack(side='left')
        entry = ttk.Entry(row)
        entry.insert(0, value)
        entry.pack(side='left', fill='x', expand=True)
        if command:
            ttk.Button(row, text=button, command=command).pack(side='left', padx=(5, 0))
        return entry

    def combo_row(self, parent, title, variable, choices, editable=False):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=4)
        ttk.Label(row, text=title, width=22).pack(side='left')
        combo = ttk.Combobox(row, textvariable=variable, values=choices, state='normal' if editable else 'readonly')
        combo.pack(side='left', fill='x', expand=True)
        return combo

    def open_settings(self, section=None):
        if section in self.settings_sections:
            self.show_settings_section(section)
        self.show_page('Settings')

    def hide_settings(self):
        self.update_preset_summary()
        try:
            self.persist_settings()
        except OSError as error:
            self.log('Could not save settings: '+str(error), 'WARNING')
        self.show_page('Movies')

    def show_custom_settings(self):
        self.open_settings('Picture & sound')

    def update_preset_summary(self):
        self.sync_output_controls()
        preset = self.core_preset_var.get()
        mode = self.video_mode_var.get()
        engine = ENGINE_NAMES.get(self.engine_var.get(), 'Auto GPU / CPU')
        if mode in ('original_copy', 'Original quality (copy source MKV)'):
            self.preset_summary.set('Original MKV · exact copy · all video, audio & subtitles preserved')
            return
        if mode in AUTO_VIDEO_MODES:
            self.preset_summary.set('DVD: 2,500 · 1080p: 19,000 · 4K: 40,000 kbps · ' + engine)
            return
        if 'Custom Target' in mode or mode == 'custom_bitrate':
            quality = self.video_bitrate_entry.get() + ' kbps target'
        elif 'Constant Quality' in mode or mode == 'custom_rf':
            quality = 'RF ' + self.video_rf_entry.get() + ' · variable bitrate'
        else:
            quality = 'HandBrake preset quality'
        self.preset_summary.set(('Automatic resolution selection' if preset == 'auto' else preset) + '  ·  ' + quality + ' · ' + engine)

    def toggle_theme(self):
        self.motion.settle()
        previous = COLORS
        self.theme_mode = 'dark' if self.theme_mode == 'light' else 'light'
        set_palette(self.theme_mode)
        self.settings['theme'] = self.theme_mode
        self.setup_theme()
        replacements = {previous[key]: value for key, value in COLORS.items()}
        def repaint(widget):
            # Native Tk containers do not inherit ttk styles.
            if isinstance(widget, (tk.Tk, tk.Toplevel, tk.Frame, tk.Canvas)):
                widget.configure(background=BG)
            if isinstance(widget, tk.Text):
                widget.configure(background=COLORS['entry'], foreground=INK, insertbackground=INK)
            elif isinstance(widget, ttk.Label):
                current = str(widget.cget('foreground'))
                if current in replacements:
                    widget.configure(foreground=replacements[current])
            if isinstance(widget, SoftCard):
                widget.draw()
            if isinstance(widget, ttk.Combobox):
                # Update listboxes that Tk already created before the theme changed.
                popdown = widget.tk.call('ttk::combobox::PopdownWindow', str(widget))
                widget.tk.call(str(popdown)+'.f.l', 'configure', '-background', COLORS['entry'],
                               '-foreground', INK, '-selectbackground', COLORS['selection'], '-selectforeground', INK)
            for child in widget.winfo_children():
                repaint(child)
        repaint(self)
        self.show_page(self.active_page)
        for tag, color in [('SUCCESS', COLORS['success']), ('ERROR', COLORS['danger']),
                           ('INFO', ACCENT), ('WARNING', COLORS['warning']), ('DIM', MUTED)]:
            self.log_text.tag_config(tag, foreground=color)
        try:
            write_json_atomic(SETTINGS_FILE, self.settings)
        except OSError as error:
            self.log('Could not save theme preference: '+str(error), 'WARNING')

    def first_run_hint(self):
        if not find_tool('HandBrakeCLI'):
            self.lbl_task_status.configure(text='First time here? Open Tool setup to add HandBrakeCLI, then scan a movie.')
            self.show_page('Tool setup')

    def check_encoders_ui(self):
        cli = self.cli_entry.get().strip()
        if not os.path.isfile(cli):
            cli = find_tool('HandBrakeCLI')
        if not cli:
            self.encoder_status.set('Add HandBrakeCLI in Tool setup to check encoders.')
            self.open_setup()
            return
        self.encoder_status.set('Checking this computer…')
        def worker():
            try:
                available = probe_encoders(cli)
                labels = [ENGINE_NAMES[engine] for engine, codes in ENCODERS.items() if codes[0] in available]
                message = 'Available: ' + ', '.join(labels) + '. 4K needs a compatible 10-bit HEVC encoder.'
            except Exception as error:
                message = 'Encoder check: '+str(error)
            self.ui_queue.put(lambda: self.encoder_status.set(message))
        threading.Thread(target=worker, daemon=True).start()

    def export_activity(self):
        path = filedialog.asksaveasfilename(parent=self, title='Save troubleshooting log', defaultextension='.txt', filetypes=[('Text', '*.txt')])
        if path:
            try:
                Path(path).write_text(f'Library Studio {APP_VERSION} | {platform.system()} {platform.machine()}\n' + self.log_text.get('1.0', 'end'), encoding='utf-8')
                self.log('Activity saved: '+path, 'SUCCESS')
            except OSError as error:
                messagebox.showerror('Save activity', str(error), parent=self)

    def open_setup(self):
        self.show_page('Tool setup')

    def report_callback_exception(self, exc_type, error, tb):
        import traceback
        details = ''.join(traceback.format_exception(exc_type, error, tb))
        if hasattr(self, 'log_queue'):
            self.log(details, 'ERROR')
        if hasattr(self, 'lbl_task_status'):
            self.lbl_task_status.configure(text='Something went wrong. See Activity for details: '+str(error))

    def persist_settings(self):
        self.settings.update({'HandBrakeCLI': self.cli_entry.get().strip(), 'movie_destination': self.dst_entry.get().strip(),
                              'online_year': self.online_year.get(), 'reduce_motion': self.reduce_motion.get(), 'theme': self.theme_mode, 'movie_engine': self.engine_var.get(), 'encoder_fallback': self.fallback_var.get(), 'responsive_cpu': self.responsive_var.get()})
        write_json_atomic(SETTINGS_FILE, self.settings)

    def close_app(self):
        music = getattr(self, 'music_window', None)
        setup = getattr(self, 'setup_window', None)
        if self.is_running or self.is_scanning or (music and music.winfo_exists() and music.busy) or (setup and setup.winfo_exists() and setup.busy):
            self.cancel_transcoding()
            if music and music.winfo_exists():
                music.stop.set()
            if setup and setup.winfo_exists():
                setup.cancel.set()
            self.lbl_task_status.configure(text='Stopping safely…')
            self.after(200, self.close_app)
            return
        try:
            self.persist_settings()
        except OSError:
            pass
        self.destroy()

    # --- Presets Manager Methods ---
    def load_presets(self):
        presets = {name: dict(value) for name, value in DEFAULT_PRESETS.items()}
        legacy = os.path.join(SCRIPT_DIR, "transcode_movie_presets.json")
        source = PRESETS_FILE if os.path.exists(PRESETS_FILE) else legacy
        if os.path.exists(source):
            try:
                with open(source, "r", encoding="utf-8") as f:
                    user_presets = json.load(f)
                    if isinstance(user_presets, dict):
                        presets.update({k:v for k,v in user_presets.items() if isinstance(v, dict)
                                        and k not in RETIRED_PRESETS and k not in DEFAULT_PRESETS and k != CUSTOM_PRESET})
            except Exception as e:
                print(f"Error loading presets file: {e}")
        return presets

    def save_presets_to_disk(self):
        try:
            write_json_atomic(PRESETS_FILE, self.presets)
        except Exception as e:
            messagebox.showerror("Save Preset Error", f"Failed to save presets: {e}")

    def on_saved_profile_selected(self, event=None):
        self.preset_combo.set(self.saved_profile_combo.get())
        self.on_preset_selected(event)

    def on_output_format_selected(self, event=None):
        chosen = self.output_format_var.get()
        self.output_custom_choice = None
        if chosen == FORMAT_CUSTOM:
            self.open_settings('Picture & sound')
            self.sync_output_controls()
            return
        options = list(OUTPUT_CHOICES[chosen])
        previous = self.output_rate_var.get()
        preferred = next((option for option in options if previous.startswith('High') and option.startswith('High')), options[0])
        self.output_rate_combo.configure(values=options, state='readonly')
        self.output_rate_var.set(preferred)
        self.on_output_rate_selected()

    def on_output_rate_selected(self, event=None):
        chosen, rate = self.output_format_var.get(), self.output_rate_var.get()
        self.output_custom_choice = rate if rate in (RATE_CUSTOM, RATE_QUALITY) else None
        if chosen in (FORMAT_AUTO, FORMAT_ORIGINAL):
            self.load_preset_values(next(iter(OUTPUT_CHOICES[chosen].values())))
            return
        if chosen not in FORMAT_BASE:
            self.open_settings('Picture & sound')
            return
        if rate == 'Recommended by source':
            self.core_preset_var.set(FORMAT_BASE[chosen])
            self.video_mode_var.set(AUTO_VIDEO_MODE)
            self.preset_combo.set(CUSTOM_PRESET)
            self.on_video_mode_change()
            self.update_preset_summary()
            return
        profile = OUTPUT_CHOICES[chosen].get(rate)
        if profile:
            self.load_preset_values(profile)
            return
        self.core_preset_var.set(FORMAT_BASE[chosen])
        self.preset_combo.set(CUSTOM_PRESET)
        if rate == RATE_DEFAULT:
            self.video_mode_var.set('Use HandBrake Preset Bitrate/RF (Recommended)')
        elif rate == RATE_QUALITY:
            self.video_mode_var.set('Custom Constant Quality (RF / CQ)')
        else:
            self.video_mode_var.set('Custom Target Bitrate (kbps)')
        self.on_video_mode_change()
        self.update_preset_summary()
        if rate in (RATE_CUSTOM, RATE_QUALITY):
            self.output_value_entry.focus_set()
            self.output_value_entry.select_range(0, 'end')

    def on_output_value_changed(self, *args):
        if self.syncing_output or not hasattr(self, 'video_bitrate_entry'):
            return
        rate = self.output_rate_var.get()
        entry = self.video_rf_entry if rate == RATE_QUALITY else self.video_bitrate_entry
        if rate not in (RATE_CUSTOM, RATE_QUALITY):
            return
        entry.configure(state='normal')
        entry.delete(0, 'end')
        entry.insert(0, self.output_value_var.get())
        self.update_preset_summary()

    def sync_output_controls(self):
        if self.syncing_output:
            return
        self.syncing_output = True
        try:
            mode, preset = self.video_mode_var.get(), self.core_preset_var.get()
            chosen = format_from_settings(preset, mode)
            options = list(OUTPUT_CHOICES[chosen])
            editable = chosen in FORMAT_BASE
            if editable:
                options += [RATE_CUSTOM, RATE_QUALITY]
            if chosen == FORMAT_ORIGINAL:
                rate = options[0]
            elif mode in AUTO_VIDEO_MODES:
                rate = 'Recommended by source'
                if rate not in options:
                    options.insert(0, rate)
            elif mode == 'custom_rf' or 'Constant Quality' in mode:
                rate = RATE_QUALITY
            elif mode == 'custom_bitrate' or 'Bitrate (kbps)' in mode:
                rate = RATE_CUSTOM
                candidates = OUTPUT_CHOICES[chosen].items() if self.output_custom_choice != RATE_CUSTOM else ()
                for label, name in candidates:
                    profile = DEFAULT_PRESETS.get(name, {})
                    base = {'DVD':'Fast 480p30', 'Blue-ray basic':'Fast 1080p30', 'Blue-ray':'HQ 1080p30 Surround'}.get(profile.get('preset'), profile.get('preset'))
                    if profile.get('video_mode') == 'custom_bitrate' and base == preset and profile.get('video_bitrate') == self.video_bitrate_entry.get():
                        rate = label
                        break
            else:
                rate = RATE_DEFAULT
            if rate not in options:
                options.append(rate)
            self.output_format_var.set(chosen)
            self.output_rate_combo.configure(values=options, state='readonly' if editable else 'disabled')
            self.output_rate_var.set(rate)
            if rate in (RATE_CUSTOM, RATE_QUALITY):
                self.output_value_label.configure(text='RF / CQ' if rate == RATE_QUALITY else 'Bitrate (kbps)')
                self.output_value_var.set((self.video_rf_entry if rate == RATE_QUALITY else self.video_bitrate_entry).get())
                self.output_value_row.pack(fill='x', pady=(2, 4), before=self.profile_summary_label)
            else:
                self.output_value_row.pack_forget()
            current = self.preset_combo.get()
            saved = current in self.presets and current not in DEFAULT_PRESETS and current != CUSTOM_PRESET
            self.saved_profile_combo.set(current if saved else 'Select a saved profile…')
            if not saved:
                profile = OUTPUT_CHOICES[chosen].get(rate)
                if profile:
                    self.preset_combo.set(profile)
                elif chosen in FORMAT_BASE:
                    resolution = {FORMAT_DVD:'DVD 480p', FORMAT_HD:'720p', FORMAT_BLURAY:'1080p Blu-ray', FORMAT_4K:'4K Blu-ray'}[chosen]
                    quality = (self.video_rf_entry.get()+' RF' if rate == RATE_QUALITY else self.video_bitrate_entry.get()+' kbps' if rate == RATE_CUSTOM else 'HandBrake default')
                    self.preset_combo.set(resolution+' ('+quality+')')
                elif chosen == FORMAT_AUTO and mode not in AUTO_VIDEO_MODES:
                    quality = (self.video_rf_entry.get()+' RF' if rate == RATE_QUALITY else self.video_bitrate_entry.get()+' kbps' if rate == RATE_CUSTOM else 'HandBrake default')
                    self.preset_combo.set('Automatic resolution ('+quality+')')
        finally:
            self.syncing_output = False

    def on_preset_selected(self, event=None):
        name = self.preset_combo.get()
        if name == CUSTOM_PRESET:
            if ('Default' in self.video_mode_var.get() or 'Recommended' in self.video_mode_var.get()
                    or self.video_mode_var.get() in AUTO_VIDEO_MODES):
                self.video_mode_var.set('Custom Target Bitrate (kbps)')
            self.on_video_mode_change()
            self.show_custom_settings()
        elif name in self.presets:
            self.load_preset_values(name)

    def load_preset_values(self, name):
        p = self.presets.get(name)
        if not p:
            return
        mode = p.get('video_mode', '')
        self.output_custom_choice = (RATE_CUSTOM if name not in DEFAULT_PRESETS and (mode == 'custom_bitrate' or 'Bitrate (kbps)' in mode) else None)
        self.preset_combo.set(name)
        aliases = {"DVD":"Fast 480p30", "Blue-ray basic":"Fast 1080p30", "Blue-ray":"HQ 1080p30 Surround"}
        value = aliases.get(p.get('preset'), p.get('preset', 'auto'))
        self.core_preset_var.set('Fast 480p30' if value == 'Fast 576p25' else value)
        modes = {"auto_bitrate":AUTO_VIDEO_MODE, "Automatic bitrate (DVD 2500 / HD 19000 kbps)":AUTO_VIDEO_MODE, "original_copy":"Original quality (copy source MKV)", "preset_default":"Use HandBrake Preset Bitrate/RF (Recommended)", "custom_bitrate":"Custom Target Bitrate (kbps)", "custom_rf":"Custom Constant Quality (RF / CQ)"}
        self.video_mode_var.set(modes.get(p.get("video_mode"), p.get("video_mode", modes["preset_default"])))
        self.video_bitrate_entry.configure(state="normal")
        self.video_bitrate_entry.delete(0, "end")
        self.video_bitrate_entry.insert(0, p.get("video_bitrate", "4000"))
        self.video_rf_entry.configure(state="normal")
        self.video_rf_entry.delete(0, "end")
        self.video_rf_entry.insert(0, p.get("video_rf", "22"))
        audio = p.get("audio_mode", "smart_surround")
        self.audio_mode_var.set("Smart Surround (Stereo AAC + 5.1 AC3 if present)" if audio == "smart_surround" else audio)
        self.stereo_kbps_combo.set(p.get("audio_stereo_bitrate", "192"))
        self.surround_kbps_combo.set(p.get("audio_ac3_bitrate", "512"))
        extra = p.get("extras_mode", "subfolder_numbered")
        extra = {"subfolder_numbered":"Subfolder: extras / numbered", "subfolder_named":"Subfolder: extras / Original filenames"}.get(extra, extra)
        self.extras_mode_var.set(re.sub(r"P[l]ex|E[m]by", "Jellyfin", extra))
        self.min_size_var.set(p.get("extras_min_size_mb", 10))
        self.dedup_extras_var.set(p.get("extras_dedup", True))
        self.include_subfolder_extras_var.set(p.get("extras_include_subfolder", True))
        self.remote_scratch_var.set(p.get("remote_scratch", True))
        if name not in DEFAULT_PRESETS and "dst_root" in p:
            self.dst_entry.delete(0, "end")
            self.dst_entry.insert(0, p["dst_root"])
        self.on_video_mode_change()
        self.update_preset_summary()

    def save_current_preset(self):
        from tkinter import simpledialog
        if self.is_running or self.is_scanning:
            return
        current_name = self.preset_combo.get()
        suggested = "My custom preset" if current_name in DEFAULT_PRESETS or current_name == CUSTOM_PRESET else current_name
        name = simpledialog.askstring("Save Preset", "Enter a name for this preset profile:", initialvalue=suggested, parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in DEFAULT_PRESETS or name == CUSTOM_PRESET:
            messagebox.showinfo("Choose a custom name", "Use a new name to keep the built-in profiles intact.", parent=self)
            return

        preset_data = {
            "preset": self.core_preset_var.get(),
            "video_mode": self.video_mode_var.get(),
            "video_bitrate": self.video_bitrate_entry.get().strip(),
            "video_rf": self.video_rf_entry.get().strip(),
            "audio_mode": self.audio_mode_var.get(),
            "audio_stereo_bitrate": self.stereo_kbps_combo.get(),
            "audio_ac3_bitrate": self.surround_kbps_combo.get(),
            "extras_mode": self.extras_mode_var.get(),
            "extras_min_size_mb": self.min_size_var.get(),
            "extras_dedup": self.dedup_extras_var.get(),
            "extras_include_subfolder": self.include_subfolder_extras_var.get(),
            "remote_scratch": self.remote_scratch_var.get(),
            "dst_root": self.dst_entry.get().strip()
        }

        self.presets[name] = preset_data
        self.save_presets_to_disk()
        self.saved_profile_combo['values'] = [*[name for name in self.presets if name not in DEFAULT_PRESETS], CUSTOM_PRESET]
        self.preset_combo.set(name)
        self.update_preset_summary()
        self.log(f"Saved preset profile '{name}'.", "SUCCESS")

    def delete_current_preset(self):
        name = self.preset_combo.get()
        if self.is_running or self.is_scanning or name not in self.presets:
            return
        if name in DEFAULT_PRESETS:
            messagebox.showwarning("Default Preset", f"'{name}' is a built-in default preset and cannot be deleted.")
            return
        if messagebox.askyesno("Delete Preset", f"Are you sure you want to delete preset '{name}'?"):
            del self.presets[name]
            self.save_presets_to_disk()
            self.saved_profile_combo['values'] = [*[name for name in self.presets if name not in DEFAULT_PRESETS], CUSTOM_PRESET]
            self.load_preset_values(next(iter(self.presets)))
            self.log(f"Deleted preset '{name}'.", "INFO")

    def on_video_mode_change(self, event=None):
        mode = self.video_mode_var.get()
        if event is not None:
            self.output_custom_choice = (RATE_CUSTOM if mode == 'custom_bitrate' or 'Bitrate (kbps)' in mode else RATE_QUALITY if mode == 'custom_rf' or 'Constant Quality' in mode else None)
        if "Default" in mode or "Recommended" in mode or mode in AUTO_VIDEO_MODES or mode in ("preset_default", "original_copy", "Original quality (copy source MKV)"):
            self.video_bitrate_entry.configure(state="disabled")
            self.video_rf_entry.configure(state="disabled")
        elif "Bitrate" in mode or mode == "custom_bitrate":
            self.video_bitrate_entry.configure(state="normal")
            self.video_rf_entry.configure(state="disabled")
        else:
            self.video_bitrate_entry.configure(state="disabled")
            self.video_rf_entry.configure(state="normal")
        if event is not None:
            self.update_preset_summary()

    # --- UI Browse Handlers ---
    def browse_source_folder(self):
        path = filedialog.askdirectory(title="Select Source Movie Folder or Parent Folder")
        if path:
            self.src_entry.delete(0, "end")
            self.src_entry.insert(0, os.path.abspath(path))
            self.auto_fill_name_year(path)

    def browse_source_file(self):
        path = filedialog.askopenfilename(title="Select Source MKV File", filetypes=[("MKV Video Files", "*.mkv"), ("All Files", "*.*")])
        if path:
            self.src_entry.delete(0, "end")
            self.src_entry.insert(0, os.path.abspath(path))
            self.auto_fill_name_year(os.path.splitext(path)[0])

    def browse_dest_folder(self):
        path = filedialog.askdirectory(title="Select Destination Root Folder")
        if path:
            self.dst_entry.delete(0, "end")
            self.dst_entry.insert(0, os.path.abspath(path))

    def browse_cli_file(self):
        path = filedialog.askopenfilename(title='Select HandBrakeCLI', filetypes=[('HandBrakeCLI', 'HandBrakeCLI.exe' if os.name == 'nt' else 'HandBrakeCLI'), ('All files', '*')])
        if path:
            self.cli_entry.delete(0, "end")
            self.cli_entry.insert(0, os.path.abspath(path))

    def open_dest_folder(self):
        dst = self.dst_entry.get().strip()
        if os.path.exists(dst):
            try:
                open_folder(dst)
            except OSError as error:
                messagebox.showerror('Open folder', str(error), parent=self)
        else:
            messagebox.showinfo("Open Folder", f"Folder does not exist yet:\n{dst}")

    def auto_fill_name_year(self, folder_path):
        basename = os.path.basename(os.path.normpath(folder_path))
        match = re.match(r"^(.+?)\s*\((\d{4})\)$", basename)
        if match:
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, match.group(1).strip())
            self.year_entry.delete(0, "end")
            self.year_entry.insert(0, match.group(2))
        else:
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, basename)
            self.year_entry.delete(0, "end")

    def lookup_year_ui(self):
        title = self.name_entry.get().strip()
        if not title:
            messagebox.showinfo("Lookup Year", "Please enter a Movie Title first.")
            return
        self.log(f"Looking up release year online for '{title}'...", "INFO")
        
        def worker():
            yr = lookup_movie_year(title)
            if yr:
                self.ui_queue.put(lambda: [self.year_entry.delete(0, "end"), self.year_entry.insert(0, yr),
                                       self.log(f"Found IMDb Year: {yr}", "SUCCESS")])
            else:
                self.ui_queue.put(lambda: self.log(f"Could not find year for '{title}' on IMDb.", "WARNING"))

        threading.Thread(target=worker, daemon=True).start()

    # --- Scanning & Queue Preview ---
    def movie_settings(self):
        settings = {'src': self.src_entry.get().strip().strip('\'"'), 'dst': self.dst_entry.get().strip().strip('\'"'),
                    'cli': self.cli_entry.get().strip(), 'preset': self.core_preset_var.get(),
                    'name': self.name_entry.get().strip(), 'year': self.year_entry.get().strip(),
                    'extras': self.extras_mode_var.get(), 'min_size': self.min_size_var.get(),
                    'dedup': self.dedup_extras_var.get(), 'include_sub': self.include_subfolder_extras_var.get(),
                    'online_year': self.online_year.get(), 'video': self.video_mode_var.get(),
                    'bitrate': self.video_bitrate_entry.get().strip(), 'rf': self.video_rf_entry.get().strip(),
                    'audio': self.audio_mode_var.get(), 'stereo': self.stereo_kbps_combo.get(),
                    'surround': self.surround_kbps_combo.get(), 'scratch': self.remote_scratch_var.get(), 'engine': self.engine_var.get(), 'fallback': self.fallback_var.get(), 'responsive': self.responsive_var.get(), 'profile': self.preset_combo.get()}
        if not os.path.exists(settings['src']):
            raise ValueError('Choose an existing source file or folder.')
        if os.path.isfile(settings['src']) and not settings['src'].lower().endswith('.mkv'):
            raise ValueError('Choose an MKV movie file.')
        if not settings['dst']:
            raise ValueError('Choose a destination folder.')
        if not os.path.isfile(settings['cli']):
            settings['cli'] = find_tool('HandBrakeCLI')
        if not os.path.isfile(settings['cli']):
            raise ValueError('HandBrakeCLI is missing. Open Tool setup to download or choose the command-line tool.')
        if settings['min_size'] < 0:
            raise ValueError('Minimum extras size must be zero or greater.')
        if settings['year'] and not re.fullmatch(r'\d{4}', settings['year']):
            raise ValueError('Enter a four-digit year or leave it blank.')
        if settings['video'] == 'custom_bitrate' or 'Bitrate (kbps)' in settings['video']:
            if not settings['bitrate'].isdigit() or not 100 <= int(settings['bitrate']) <= 200000:
                raise ValueError('Video bitrate must be between 100 and 200000 kbps.')
        if settings['video'] == 'custom_rf' or 'Constant Quality' in settings['video']:
            try:
                valid = 0 <= float(settings['rf']) <= 51
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('Video RF must be between 0 and 51.')
        return settings

    def movie_busy(self, active):
        self.btn_start.configure(state='disabled' if active else 'normal')
        self.btn_scan.configure(state='disabled' if active else 'normal')
        self.btn_cancel.configure(state='normal' if active else 'disabled')
        self.btn_clear.configure(state='disabled' if active else 'normal')
        self.btn_scan.configure(text='Scanning…' if self.is_scanning else 'Scan & preview')
        self.btn_start.configure(text='Converting…' if self.is_running else 'Start conversion  →')
        if self.is_scanning:
            self.motion.cancel('progress:' + str(self.progress_bar))
            self.progress_bar.configure(mode='indeterminate')
            if not self.reduce_motion.get():
                self.progress_bar.start(20)
        else:
            scanning = str(self.progress_bar.cget('mode')) == 'indeterminate'
            self.progress_bar.stop()
            self.progress_bar.configure(mode='determinate')
            if scanning:
                self.animate_progress(self.progress_bar, 0, immediate=True)
        self.update_queue_selection()

    def start_preview_scan(self, auto_start=False):
        if self.is_running or self.is_scanning:
            return
        try:
            settings = self.movie_settings()
            self.persist_settings()
        except (ValueError, OSError, tk.TclError) as error:
            messagebox.showerror('Check movie settings', str(error), parent=self)
            return
        self.clear_queue()
        self.cancel_requested = False
        self.is_scanning = True
        self.movie_busy(True)
        self.lbl_task_status.configure(text='Scanning movie files…')
        self.lbl_eta.configure(text='')
        self.animate_progress(self.progress_bar, 0, immediate=True)
        def worker():
            try:
                jobs = self.scan_movies(settings)
                if self.cancel_requested:
                    raise InterruptedError('Scan cancelled.')
                self.ui_queue.put(lambda: self.scan_finished(jobs, settings, auto_start))
            except Exception as error:
                self.ui_queue.put(lambda message=str(error): self.scan_failed(message))
        threading.Thread(target=worker, daemon=True).start()

    def scan_finished(self, jobs, settings, auto_start):
        self.is_scanning = False
        self.movie_busy(False)
        self.jobs = jobs
        self.queue_settings = settings
        self._render_scanned_jobs()
        if auto_start and jobs and not self.cancel_requested:
            self.start_transcode_thread()

    def scan_failed(self, message):
        self.is_scanning = False
        self.movie_busy(False)
        self.lbl_task_status.configure(text=message)
        self.log(message, 'ERROR' if not self.cancel_requested else 'WARNING')

    def scan_movies(self, settings):
        available = ({'x264', 'x265_10bit'} if is_original_copy(settings) or settings.get('engine', ENGINE_CPU) == ENGINE_CPU
                     else probe_encoders(settings['cli'], lambda: self.cancel_requested))
        src = os.path.abspath(settings['src'])
        single = src if os.path.isfile(src) else None
        root = os.path.dirname(src) if single else src
        if single or any(f.lower().endswith('.mkv') for f in os.listdir(root)):
            folders = [root]
        else:
            folders = [os.path.join(root, f) for f in sorted(os.listdir(root))
                       if not f.startswith('$') and f.lower() not in ('system volume information', 'recycle.bin')
                       and os.path.isdir(os.path.join(root, f))]
        jobs = []
        for folder in folders:
            self.check_cancel()
            try:
                files = [os.path.join(folder, f) for f in os.listdir(folder)
                         if f.lower().endswith('.mkv') and os.path.isfile(os.path.join(folder, f))]
            except PermissionError:
                self.log('Skipping unreadable folder: '+folder, 'WARNING')
                continue
            if single:
                files = [single]
            if not files:
                continue
            files.sort(key=os.path.getsize, reverse=True)
            main = files[0]
            base = os.path.splitext(os.path.basename(single))[0] if single else os.path.basename(folder)
            match = re.match(r'^(.+?)\s*\((\d{4})\)$', base)
            title = settings['name'] if len(folders) == 1 and settings['name'] else (match.group(1) if match else base)
            year = settings['year'] if len(folders) == 1 and settings['year'] else (match.group(2) if match else '')
            if not year and settings['online_year']:
                year = lookup_movie_year(title) or ''
            scan = scan_file(main, settings['cli'], lambda: self.cancel_requested)
            detected, resolution = detect_preset_from_scan(scan)
            preset = detected if settings['preset'] == 'auto' else settings['preset']
            audio = parse_audio_tracks(scan)
            title = cd_safe_name(title)
            folder_name = f'{title} ({year})' if year else title
            target = os.path.join(os.path.abspath(settings['dst']), folder_name)
            def job(path, kind, destination, info):
                if is_original_copy(settings):
                    destination = os.path.splitext(destination)[0] + '.mkv'
                    info += ' · Original quality (exact copy)'
                elif settings['video'] in AUTO_VIDEO_MODES:
                    info += ' · ' + f'{int(automatic_video_bitrate(detected)):,}' + ' kbps'
                plan = {}
                if not is_original_copy(settings):
                    plan['engine'], plan['encoder'] = select_encoder(settings, {'preset': preset}, available)
                return {**plan, 'type': kind, 'name': os.path.basename(path), 'size': format_size(os.path.getsize(path)),
                        'info': info, 'preset': preset, 'detected_preset': detected, 'src': path, 'dst': destination, 'target_dir': target}
            jobs.append(job(main, 'Main Feature', os.path.join(target, folder_name+'.mp4'),
                            resolution+' · '+('Surround' if any(t['is_5point1'] for t in audio) else 'Stereo')))
            extras = files[1:] if not single and 'Skip' not in settings['extras'] else []
            if not single and 'Skip' not in settings['extras'] and settings['include_sub']:
                for name in os.listdir(folder):
                    extra_folder = os.path.join(folder, name)
                    if name.lower() == 'extras' and os.path.isdir(extra_folder):
                        extras.extend(os.path.join(extra_folder, f) for f in os.listdir(extra_folder)
                                      if f.lower().endswith('.mkv') and os.path.isfile(os.path.join(extra_folder, f)))
            seen = {os.path.getsize(main): [main]}
            hashes = {}
            def digest(path):
                if path not in hashes:
                    value = hashlib.sha256()
                    with open(path, 'rb') as file:
                        for block in iter(lambda: file.read(1024*1024), b''):
                            self.check_cancel()
                            value.update(block)
                    hashes[path] = value.digest()
                return hashes[path]
            number = 0
            for extra in sorted(extras, key=lambda p: (-os.path.getsize(p), p)):
                self.check_cancel()
                size = os.path.getsize(extra)
                if size < settings['min_size'] * 1024 * 1024:
                    continue
                if settings['dedup'] and any(digest(extra) == digest(prior) for prior in seen.get(size, [])):
                    continue
                seen.setdefault(size, []).append(extra)
                number += 1
                if 'Same Folder' in settings['extras']:
                    destination = os.path.join(target, f'{title} - Extra {number}-featurette.mp4')
                elif 'Original' in settings['extras'] or 'Named' in settings['extras']:
                    destination = os.path.join(target, 'extras', cd_safe_name(Path(extra).stem)+'.mp4')
                    if any(j['dst'].lower() == destination.lower() for j in jobs):
                        destination = os.path.join(target, 'extras', f'{number:02} - '+cd_safe_name(Path(extra).stem)+'.mp4')
                else:
                    destination = os.path.join(target, 'extras', f'Extra {number:02}.mp4')
                jobs.append(job(extra, f'Extra #{number}', destination, 'Bonus feature'))
        if not jobs:
            raise ValueError('No MKV files found. Choose a file, a movie folder, or a parent containing movie folders.')
        outputs = [os.path.normcase(j['dst']) for j in jobs]
        if len(outputs) != len(set(outputs)):
            raise ValueError('Two movies resolve to the same output name. Scan them separately with distinct title overrides.')
        return jobs

    def check_cancel(self):
        if self.cancel_requested:
            raise InterruptedError('Operation cancelled.')

    def refresh_queue_summary(self):
        count = len(self.jobs)
        total_bytes = 0
        for job in self.jobs:
            try:
                total_bytes += os.path.getsize(job['src'])
            except OSError:
                pass
        self.queue_count.set(f'{count} file' + ('s' if count != 1 else '') + (f' · {format_size(total_bytes)} source' if count else ''))
        self.update_queue_selection()
        if count:
            self.empty_queue.place_forget()
            self.queue_hint.configure(text=f'{count} file{"s" if count != 1 else ""} ready. Existing output files will be skipped.')
        else:
            self.empty_queue.place(relx=.5, rely=.58, anchor='center')
            self.queue_hint.configure(text='Your source files stay untouched. Converted movies are saved to your library.')

    def clear_queue(self):
        if self.is_running or self.is_scanning:
            return
        self.tree.delete(*self.tree.get_children())
        self.jobs = []
        self.queue_settings = None
        self.queue_hint.configure(text='Your queue is empty. Choose a source above, then scan to preview the output.')
        self.refresh_queue_summary()
        self.lbl_task_status.configure(text='Queue cleared.')
        self.lbl_stats.configure(text='')
        self.lbl_eta.configure(text='')

    def remove_selected_item(self):
        if self.is_running or self.is_scanning:
            return
        selected = self.tree.selection()
        if selected:
            index = self.tree.index(selected[0])
            self.tree.delete(selected[0])
            del self.jobs[index]
            self.refresh_queue_summary()
            self.lbl_task_status.configure(text=f'{len(self.jobs)} items in queue.')

    def start_transcode_thread(self):
        if self.is_running or self.is_scanning:
            return
        try:
            settings = self.movie_settings()
        except (ValueError, tk.TclError) as error:
            messagebox.showerror('Check settings', str(error), parent=self)
            return
        # Rebuild destinations when input/naming settings change; quality settings can change independently.
        keys = ('src','dst','preset','name','year','extras','min_size','dedup','include_sub','online_year','cli','engine')
        if (not self.jobs or not self.queue_settings or any(settings[k] != self.queue_settings[k] for k in keys)
                or is_original_copy(settings) != is_original_copy(self.queue_settings)):
            self.start_preview_scan(auto_start=True)
            return
        self.is_running = True
        self.cancel_requested = False
        self.movie_busy(True)
        self.queue_settings = settings
        jobs = [dict(job) for job in self.jobs]
        self.animate_progress(self.progress_bar, 0, immediate=True)
        self.lbl_stats.configure(text='')
        self.lbl_eta.configure(text='Time left · Calculating…')
        threading.Thread(target=self._transcode_worker, args=(settings, jobs), daemon=True).start()

    def cancel_transcoding(self):
        if not self.is_running and not self.is_scanning:
            return
        self.cancel_requested = True
        process = self.current_process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        self.lbl_task_status.configure(text='Cancelling safely…')
        self.lbl_eta.configure(text='Time left · Cancelling…')

    def _encode_job(self, settings, job, index, count, output, available):
        aliases = {'DVD': 'Fast 480p30', 'Blue-ray basic': 'Fast 1080p30', 'Blue-ray': 'HQ 1080p30 Surround'}
        preset = aliases.get(job['preset'], job['preset'])
        audio_args = []
        audio_mode = settings['audio']
        if 'Smart' in audio_mode or audio_mode == 'smart_surround' or 'Passthrough' in audio_mode:
            tracks = parse_audio_tracks(scan_file(job['src'], settings['cli'], lambda: self.cancel_requested))
            if 'Passthrough' in audio_mode:
                if tracks:
                    audio_args = ['-a', ','.join(str(t['index']) for t in tracks), '-E', 'copy']
            elif tracks:
                english = [t for t in tracks if t['lang'] == 'eng']
                track = (english or tracks)[0]
                number = str(track['index'])
                if track['is_5point1']:
                    audio_args = ['-a', number+','+number, '-E', 'av_aac,ac3', '-B', settings['stereo']+','+settings['surround'], '-6', 'stereo,5point1']
                else:
                    audio_args = ['-a', number, '-E', 'av_aac', '-B', settings['stereo'], '-6', 'stereo']
        elif 'Stereo' in audio_mode:
            audio_args = ['-a', '1', '-E', 'av_aac', '-B', settings['stereo'], '-6', 'stereo']
        job['engine'], job['encoder'] = select_encoder(settings, job, available)
        requested = settings.get('engine', ENGINE_AUTO)
        if requested not in (ENGINE_AUTO, job['engine']):
            self.log(f"{ENGINE_NAMES.get(requested, requested)} is unavailable for this profile. Using CPU.", 'WARNING')
        for attempt in range(2):
            self.check_cancel()
            command = [settings['cli'], '-Z', preset, '-i', job['src'], '-o', output]
            if preset not in OFFICIAL_PRESETS:
                command.append('--preset-import-gui')
            command += hardware_encoding_args(settings, job) + video_encoding_args(settings, job) + audio_args
            label = ENGINE_NAMES[job['engine']]
            self.log(f"Converting {index+1}/{count}: {job['name']} · {label}")
            self.ui_queue.put(lambda label=label: self.lbl_task_status.configure(text=f"Converting {index+1}/{count}: {job['name']} · {label}"))
            if hasattr(self, 'update_job_encoder'):
                self.ui_queue.put(lambda engine=job['engine'], encoder=job['encoder']: self.update_job_encoder(index, engine, encoder))
            self.log('HandBrake command: ' + subprocess.list2cmdline(command))
            eta = getattr(self, 'conversion_eta', None)
            if eta:
                queue_eta(self, eta.start(index))
            tail, head = deque(maxlen=30), []
            started = False
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)) as process:
                self.current_process = process
                if self.cancel_requested:
                    process.terminate()
                for line in process.stdout:
                    text = line.strip()[:2000]
                    tail.append(text)
                    if len(head) < 15:
                        head.append(text)
                    if 'encavcodecInit:' in text or 'encqsvInit:' in text or 'encvtInit:' in text:
                        self.log(text)
                    percent = encoding_progress(line)
                    if percent is not None:
                        started = started or percent > 0
                        self.ui_queue.put(lambda p=percent: self._update_progress_ui((index+p/100)*100/count, p, label))
                        if eta:
                            estimate = eta.text(percent)
                            if estimate != eta.last_text:
                                eta.last_text = estimate
                                queue_eta(self, estimate)
                process.wait()
                self.current_process = None
                self.check_cancel()
                usable = os.path.isfile(output) and os.path.getsize(output) > 0
                if not process.returncode and usable:
                    return
                details = '\n'.join(head + list(tail))[-6000:]
                error = (f'HandBrake exited with code {process.returncode}. ' if process.returncode else 'No output file was created. ') + details
            # Retry startup failures once. Never restart a cancelled or long-running encode.
            if attempt == 0 and uses_hardware(settings, job) and not started and settings.get('fallback', True):
                available.discard(job['encoder'])
                self.log(f'{label} could not start. Retrying this file with CPU. Details: {error}', 'WARNING')
                if os.path.exists(output):
                    os.remove(output)
                job['engine'], job['encoder'] = select_encoder(dict(settings, engine=ENGINE_CPU), job, available)
                continue
            raise RuntimeError(error)

    def update_job_encoder(self, index, engine, encoder):
        if index < len(self.jobs):
            job = self.jobs[index]
            job.update(engine=engine, encoder=encoder)
            rows = self.tree.get_children()
            if index < len(rows):
                selected = self.queue_settings or {'video': self.video_mode_var.get()}
                self.tree.set(rows[index], 'preset', queue_profile_label(selected.get('profile', self.preset_combo.get()), job, selected))
            self.encoder_status.set('Using '+ENGINE_NAMES[engine]+' · '+encoder)

    def _transcode_worker(self, settings, jobs):
        success = skipped = failed = 0
        eta = self.conversion_eta = ConversionETA(jobs)
        queue_eta(self, 'Time left · Calculating…')
        try:
            available = ({'x264', 'x265_10bit'} if is_original_copy(settings) or settings.get('engine', ENGINE_CPU) == ENGINE_CPU
                         else probe_encoders(settings['cli'], lambda: self.cancel_requested))
            for index, job in enumerate(jobs):
                self.check_cancel()
                dst = job['dst']
                if os.path.exists(dst):
                    eta.complete(index)
                    skipped += 1
                    self.log('Existing file skipped: '+dst, 'WARNING')
                    self.ui_queue.put(lambda p=(index+1)*100/len(jobs): self.animate_progress(self.progress_bar, p))
                    continue
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    use_scratch = settings['scratch'] and is_remote_path(dst)
                    scratch_root = None if use_scratch else os.path.dirname(dst)
                    with tempfile.TemporaryDirectory(prefix='.library-encode-', dir=scratch_root) as scratch:
                        output = os.path.join(scratch, 'output.mkv' if is_original_copy(settings) else 'output.mp4')
                        if is_original_copy(settings):
                            queue_eta(self, eta.start(index))
                            self.log(f"Copying original {index+1}/{len(jobs)}: {job['name']}")
                            self.ui_queue.put(lambda n=index+1, name=job['name']: self.lbl_task_status.configure(text=f'Copying original {n}/{len(jobs)}: {name}'))
                            total = os.path.getsize(job['src'])
                            copied = 0
                            last_update = 0.0
                            with open(job['src'], 'rb') as source, open(output, 'xb') as target:
                                while True:
                                    self.check_cancel()
                                    block = source.read(4*1024*1024)
                                    if not block:
                                        break
                                    target.write(block)
                                    copied += len(block)
                                    now = time.monotonic()
                                    if now-last_update >= .1 or copied == total:
                                        percent = copied*100/max(1,total)
                                        self.ui_queue.put(lambda p=percent, i=index: self._update_progress_ui((i+p/100)*100/len(jobs), p, 'Original quality copy'))
                                        estimate = eta.text(percent)
                                        if estimate != eta.last_text:
                                            eta.last_text = estimate
                                            queue_eta(self, estimate)
                                        last_update = now
                        else:
                            self._encode_job(settings, job, index, len(jobs), output, available)
                        self.current_process = None
                        if not os.path.isfile(output) or os.path.getsize(output) == 0:
                            details = 'The original source file was empty.' if is_original_copy(settings) else 'The encoder did not produce a usable file.'
                            raise RuntimeError('No output file was created. HandBrake output:\n' + details[:6000])
                        # Stage a network copy beside its final destination so interrupted copies never look complete.
                        if use_scratch:
                            queue_eta(self, 'Time left · Transferring to destination…')
                            with tempfile.TemporaryDirectory(prefix='.library-transfer-', dir=os.path.dirname(dst)) as transfer:
                                staged = os.path.join(transfer, 'output.mp4')
                                with open(output, 'rb') as source, open(staged, 'xb') as target:
                                    while True:
                                        self.check_cancel()
                                        block = source.read(4*1024*1024)
                                        if not block:
                                            break
                                        target.write(block)
                                self.check_cancel()
                                publish_file(staged, dst)
                        else:
                            self.check_cancel()
                            publish_file(output, dst)
                    success += 1
                    self.log('Saved: '+dst, 'SUCCESS')
                except InterruptedError:
                    raise
                except Exception as error:
                    failed += 1
                    self.log(f"Failed {job['name']}: {error}", 'ERROR')
                finally:
                    self.current_process = None
                    eta.complete(index)
                self.ui_queue.put(lambda p=(index+1)*100/len(jobs): self.animate_progress(self.progress_bar, p))
        except InterruptedError:
            pass
        except Exception as error:
            failed += 1
            self.log(str(error), 'ERROR')
        finally:
            message = ('Cancelled' if self.cancel_requested else 'Finished') + f': {success} saved · {skipped} skipped · {failed} failed'
            self.ui_queue.put(lambda: self.finish_movie(message))

    def finish_movie(self, message):
        self.is_running = False
        self.current_process = None
        self.movie_busy(False)
        self.lbl_task_status.configure(text=message)
        self.lbl_stats.configure(text='')
        eta = getattr(self, 'conversion_eta', None)
        self.lbl_eta.configure(text=('Elapsed: ' + format_eta(time.monotonic() - eta.began)) if eta else '')
        self.conversion_eta = None
        self.log(message, 'WARNING' if self.cancel_requested else 'INFO')


    def _render_scanned_jobs(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for job in self.jobs:
            self.tree.insert("", "end", values=(
                job["type"],
                job["name"],
                job["size"],
                job["info"],
                queue_profile_label(self.preset_combo.get(), job, self.queue_settings or {"video": self.video_mode_var.get()}),
                job["dst"]
            ))

        self.refresh_queue_summary()
        self.lbl_task_status.config(text=f"Scan complete. {len(self.jobs)} file(s) ready in queue.")
        self.log(f"Queued {len(self.jobs)} items for transcoding.", "SUCCESS")

    def _update_progress_ui(self, overall_percent, current_percent, stats_text):
        self.animate_progress(self.progress_bar, overall_percent)
        self.lbl_stats.config(text=f'Overall {overall_percent:.0f}%  ·  Current file {current_percent:.1f}%' + (f'  ·  {stats_text}' if stats_text else ''))

    # --- Logging Queue Handler ---
    def log(self, text, tag="INFO"):
        self.log_queue.put((tag, text))

    def process_log_queue(self):
        for _ in range(200):
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception as error:
                self.log(str(error), "ERROR")
        while not self.log_queue.empty():
            tag, msg = self.log_queue.get_nowait()
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{timestamp}] ", "DIM")
            self.log_text.insert("end", f"[{tag}] ", tag)
            self.log_text.insert("end", f"{msg}\n")
            self.log_text.see("end")
            if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
                self.log_text.delete("1.0", "500.0")
        active = []
        if self.is_running or self.is_scanning:
            active.append('Movies: ' + ('scanning' if self.is_scanning else 'converting'))
        music = getattr(self, 'music_window', None)
        if music is not None and music.winfo_exists() and music.busy:
            active.append('Music CD: working')
        setup = getattr(self, 'setup_window', None)
        if setup is not None and setup.winfo_exists() and setup.busy:
            active.append('Tool setup: working')
        self.background_status.set('\n'.join(active) or 'All tasks idle')
        self.after(100, self.process_log_queue)
        self.after(250, self.first_run_hint)

    def clear_log(self):
        self.log_text.delete("1.0", "end")



class SetupWindow(tk.Frame):
    def destroy(self):
        cancel_widget_timers(self)
        super().destroy()

    def __init__(self, parent):
        super().__init__(parent.page_host)
        self.parent = parent
        self.configure(bg=BG)
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.buttons = []
        header = ttk.Frame(self, padding=(24, 20), style='Chrome.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text='Tool setup', style='ChromeTitle.TLabel').pack(anchor='w')
        ttk.Label(header, text='Set up your encoders or take your library tools with you.', style='Chrome.TLabel').pack(anchor='w', pady=4)
        area = ScrollArea(self)
        area.pack(fill='both', expand=True, padx=16, pady=16)
        body = area.body
        ttk.Label(body, text='Movies need HandBrakeCLI. MP3 CD encoding needs FFmpeg. CD ripping is Windows only.\n' + ('Python is included in this executable.' if IS_FROZEN else 'Python is ready. No pip packages needed.'), wraplength=650).pack(anchor='w', pady=8)
        self.tool_status = {}
        for name, description in [('HandBrakeCLI', 'Movie conversion'), ('ffmpeg', 'MP3 encoding')]:
            card = SoftCard(body)
            card.pack(fill='x', pady=5)
            ttk.Label(card.body, text=description, style='Section.TLabel').pack(anchor='w')
            status = tk.StringVar(value=find_tool(name) or 'Not found — set up once or choose an existing executable.')
            ttk.Label(card.body, textvariable=status, wraplength=590, style='Dim.TLabel').pack(anchor='w', pady=5)
            self.tool_status[name] = status
            row = ttk.Frame(card.body)
            row.pack(fill='x')
            for label, command in [('Download portable tool', lambda n=name: self.start_install(n)),
                                    ('Choose existing…', lambda n=name: self.choose(n))]:
                if label == 'Download portable tool' and (os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64')):
                    label = 'Get installation instructions'
                    command = lambda n=name: webbrowser.open('https://handbrake.fr/downloads2.php' if n == 'HandBrakeCLI' else 'https://ffmpeg.org/download.html')
                btn = ttk.Button(row, text=label, command=command)
                btn.pack(side='left', padx=(0, 5))
                self.buttons.append(btn)
        ttk.Label(body, text='Windows x64 downloads are SHA-256 checked. HandBrake comes from its official GitHub release;\nFFmpeg comes from gyan.dev. On macOS / Linux, install native tools and choose their executable.',
                  style='Chrome.TLabel', wraplength=640).pack(anchor='w', pady=10)
        btn = ttk.Button(body, text='Create portable folder…', style='Accent.TButton', command=self.export)
        btn.pack(anchor='w', pady=8)
        self.buttons.append(btn)
        ttk.Label(body, text='Copies this app and tools downloaded here into one movable folder.\n' + ('Python is included. Existing system tools are not copied.' if IS_FROZEN else 'Other PCs still need Python 3.10+ with Tcl/Tk. Existing system tools are not copied.'),
                  style='Chrome.TLabel', wraplength=640).pack(anchor='w')
        self.status = tk.StringVar(value='Ready. Setup downloads tools only when you click Download.')
        ttk.Label(body, textvariable=self.status, wraplength=630, style='Chrome.TLabel').pack(anchor='w', pady=14)
        self.stop_button = ttk.Button(body, text='Cancel setup', command=self.cancel.set, state='disabled')
        self.stop_button.pack(anchor='w')
        about = Foldout(body, 'About & third-party services')
        about.pack(fill='x', pady=12)
        ttk.Label(about.body, text='Library Studio '+APP_VERSION+' · '+platform.system()+'\nCreates local media files for Jellyfin and Subsonic. No server connection required.\nNormal audio CDs only; secure-rip verification is not included.\nMusicBrainz lookup sends the disc ID; movie year lookup sends the movie title.\nMetadata may be unavailable or incorrect. Review it before converting.\nDownloaded tools retain their original licenses and source information.\nCommercial distribution and commercial metadata use need their applicable licenses.',
                  wraplength=610, style='Dim.TLabel').pack(anchor='w')
        ttk.Button(about.body, text='MusicBrainz service terms', command=lambda: webbrowser.open('https://metabrainz.org/supporters/account-type')).pack(anchor='w', pady=5)
        self.after(100, self.poll)

    def choose(self, name):
        path = filedialog.askopenfilename(parent=self, title='Choose '+name, filetypes=[('Executable', '*.exe' if os.name == 'nt' else '*'), ('All files', '*')])
        if path:
            self.save_tool(name, path)

    def save_tool(self, name, path):
        self.parent.settings[name] = path
        write_json_atomic(SETTINGS_FILE, self.parent.settings)
        self.tool_status[name].set(path)
        if name == 'HandBrakeCLI':
            self.parent.cli_entry.delete(0, 'end')
            self.parent.cli_entry.insert(0, path)
        music = getattr(self.parent, 'music_window', None)
        if name == 'ffmpeg' and music is not None and music.winfo_exists() and not music.busy:
            music.ffmpeg.set(path)

    def set_busy(self, value):
        self.busy = value
        for button in self.buttons:
            button.configure(state='disabled' if value else 'normal')
        self.stop_button.configure(state='normal' if value else 'disabled')

    def start_install(self, name):
        self.cancel.clear()
        self.set_busy(True)
        self.status.set('Finding the latest verified '+name+' package…')
        def worker():
            try:
                path = install_tool(name, self.cancel, lambda text: self.events.put(('status', text)))
                self.events.put(('tool', (name, path)))
                self.events.put(('status', name+' is ready.'))
            except Exception as error:
                self.events.put(('status', 'Setup: '+str(error)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def export(self):
        folder = filedialog.askdirectory(parent=self, title='Choose a parent folder for the portable app')
        if not folder:
            return
        self.set_busy(True)
        self.stop_button.configure(state='disabled')
        self.status.set('Creating portable folder…')
        def worker():
            try:
                path = make_portable(folder)
                self.events.put(('status', 'Portable folder ready: '+path))
            except Exception as error:
                self.events.put(('status', 'Could not create portable folder: '+str(error)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'status':
                self.status.set(value)
            elif kind == 'tool':
                try:
                    self.save_tool(*value)
                except OSError as error:
                    self.status.set('Tool downloaded, but settings could not be saved: '+str(error))
            elif kind == 'done':
                self.set_busy(False)
        self.after(100, self.poll)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set('Stopping setup. Close this window when it finishes.')
        else:
            self.parent.show_page('Movies')


def run_interface_self_test(app):
    """Exercise the shipped runtime without touching media or real settings."""
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    app.update()
    for page in ('Settings', 'Music CD', 'Tool setup', 'Movies'):
        app.show_page(page)
        app.update()
        assert app.active_page == page
        assert app.pages[page].winfo_toplevel() is app
    assert not any(isinstance(widget, tk.Toplevel) for widget in app.winfo_children())
    app.open_settings('Picture & sound')
    assert app.active_settings_section == 'Picture & sound'
    for name in app.presets:
        app.load_preset_values(name)
    app.load_preset_values('Automatic (recommended)')
    assert '40,000' in app.preset_summary.get()
    # Verify the ETA label in the shipped Tk runtime without touching real media.
    eta_clock = [0.]
    estimate = ConversionETA([dict(src='missing-eta-fixture.mkv', dst='missing-eta-fixture.mp4')], clock=lambda: eta_clock[0])
    estimate.start(0)
    eta_clock[0] = 30.
    queue_eta(app, estimate.text(50))
    app.process_log_queue()
    app.update()
    assert 'File: ~30s' in app.lbl_eta.cget('text')
    assert 'Queue: ~30s' in app.lbl_eta.cget('text')
    app.lbl_eta.configure(text='')
    # Exercise the user's format-first workflow and ensure it drives the real encoder settings.
    for chosen, rate, preset, bitrate in ((FORMAT_DVD, 'Basic · 2,500 kbps', 'Fast 480p30', '2500'),
                                         (FORMAT_BLURAY, 'Basic · 19,000 kbps', 'Fast 1080p30', '19000'),
                                         (FORMAT_BLURAY, 'High · 23,000 kbps', 'HQ 1080p30 Surround', '23000'),
                                         (FORMAT_4K, 'Basic · 40,000 kbps', 'Fast 2160p60 4K HEVC', '40000'),
                                         (FORMAT_4K, 'High · 60,000 kbps', 'HQ 2160p60 4K HEVC Surround', '60000')):
        app.output_format_var.set(chosen)
        app.on_output_format_selected()
        app.output_rate_var.set(rate)
        app.on_output_rate_selected()
        assert app.core_preset_var.get() == preset
        assert app.video_bitrate_entry.get() == bitrate
        args = video_encoding_args({'video': app.video_mode_var.get(), 'bitrate': app.video_bitrate_entry.get()}, {'preset': preset})
        assert args[args.index('-b')+1] == bitrate
    app.output_format_var.set(FORMAT_BLURAY)
    app.on_output_format_selected()
    app.output_rate_var.set(RATE_CUSTOM)
    app.on_output_rate_selected()
    assert app.output_rate_var.get() == RATE_CUSTOM
    assert app.output_value_row.winfo_manager() == 'pack'
    app.output_value_var.set('19000')
    assert app.output_rate_var.get() == RATE_CUSTOM
    assert app.output_value_row.winfo_manager() == 'pack'
    app.output_value_var.set('21000')
    assert app.video_bitrate_entry.get() == '21000'
    assert '21000 kbps' in app.preset_summary.get()
    app.output_rate_var.set(RATE_QUALITY)
    app.on_output_rate_selected()
    app.output_value_var.set('18')
    assert app.video_rf_entry.get() == '18'
    app.output_format_var.set(FORMAT_HD)
    app.on_output_format_selected()
    assert app.core_preset_var.get() == 'Fast 720p30'
    assert app.output_rate_var.get() == RATE_DEFAULT
    app.output_format_var.set(FORMAT_ORIGINAL)
    app.on_output_format_selected()
    assert is_original_copy({'video': app.video_mode_var.get()})
    assert str(app.output_rate_combo.cget('state')) == 'disabled'
    app.load_preset_values('Automatic (recommended)')
    app.music_window.album.set('Navigation test')
    app.show_page('Movies')
    app.show_page('Music CD')
    assert app.music_window.album.get() == 'Navigation test'
    app.toggle_theme()
    app.update()
    app.toggle_theme()
    app.update()
    app.hide_settings()
    assert app.active_page == 'Movies'
    # Exercise retargeting and completion in the actual event loop, including the bundled runtime.
    def pump(delay=340):
        done = tk.BooleanVar(app, False)
        app.after(delay, lambda: done.set(True))
        app.wait_variable(done)
    app.reduce_motion.set(False)
    for _ in range(4):
        for name in ('Music CD', 'Settings', 'Tool setup', 'Movies'):
            app.show_page(name)
    pump()
    assert not app.motion.running, list(app.motion.running)
    assert all(page.winfo_manager() == 'grid' for page in app.pages.values())
    app.animate_progress(app.progress_bar, 75)
    pump(35)
    halfway = float(app.progress_bar.cget('value'))
    assert 0 < halfway < 75, halfway
    app.animate_progress(app.progress_bar, 100)
    pump()
    assert float(app.progress_bar.cget('value')) == 100
    button = app.page_nav['Music CD']
    app.hover_navigation(button, True)
    pump(190)
    assert ttk.Style(app).lookup(button.cget('style'), 'foreground') == ACCENT
    app.hover_navigation(button, False)
    app.show_page('Settings')
    app.reduce_motion.set(True)
    assert not app.motion.running
    assert app.preferences_window.winfo_manager() == 'grid'
    app.show_page('Music CD')
    assert not app.motion.running
    app.reduce_motion.set(False)
    app.show_page('Movies')
    pump()
    geometry = app.geometry()
    app.geometry('1180x650')
    pump()
    assert app.tree.winfo_height() >= 90, app.tree.winfo_height()
    app.output_format_var.set(FORMAT_BLURAY)
    app.on_output_format_selected()
    app.output_rate_var.set(RATE_CUSTOM)
    app.on_output_rate_selected()
    pump()
    assert app.tree.winfo_height() >= 90, app.tree.winfo_height()
    app.load_preset_values('Automatic (recommended)')
    app.geometry(geometry)
    pump()
    assert queue_profile_label('1080p Basic Blu-ray (19,000 kbps)', {'preset':'Fast 1080p30'}, {'video':'custom_bitrate'}) == '1080p Basic Blu-ray · Fast 1080p30 · CPU'
    assert queue_profile_label('Automatic (recommended)', {'preset':'Fast 2160p60 4K HEVC'}, {'video':'auto_bitrate'}) == '4K Basic Blu-ray · Fast 2160p60 4K HEVC · CPU'
    encoded_test = False
    if '--encode-test-source' in sys.argv:
        index = sys.argv.index('--encode-test-source')
        source = os.path.abspath(sys.argv[index+1])
        cli = os.path.abspath(sys.argv[index+2])
        report = os.path.abspath(sys.argv[sys.argv.index('--self-test-report')+1])
        destination = os.path.join(os.path.dirname(report), 'encode-test-output', 'fixture.mp4')
        app.src_entry.delete(0, 'end')
        app.src_entry.insert(0, source)
        app.cli_entry.delete(0, 'end')
        app.cli_entry.insert(0, cli)
        app.dst_entry.delete(0, 'end')
        app.dst_entry.insert(0, os.path.dirname(destination))
        app.load_preset_values('Automatic (recommended)')
        app.engine_var.set(sys.argv[sys.argv.index('--encode-test-engine')+1] if '--encode-test-engine' in sys.argv else ENGINE_NVIDIA)
        app.online_year.set(False)
        settings = app.movie_settings()
        scan = scan_file(source, cli)
        preset, _ = detect_preset_from_scan(scan)
        assert preset == 'Blue-ray basic', preset
        job = dict(src=source, dst=destination, name='Fixture', preset=preset, detected_preset=preset)
        # Run the real worker with the installed HandBrake, including smart-surround audio.
        if os.path.exists(destination):
            raise FileExistsError('Choose a fresh test report folder for the encoding check.')
        app._transcode_worker(settings, [job])
        pump()
        text = app.log_text.get('1.0', 'end')
        assert os.path.isfile(destination) and os.path.getsize(destination) > 0, text
        assert '1 saved' in text and '0 failed' in text, text
        assert job['engine'] == app.engine_var.get(), text
        if app.engine_var.get() == ENGINE_NVIDIA:
            assert 'nvenc_h264' in text and '--enable-hw-decoding nvdec' in text, text
            assert 'encavcodecInit: H.264 (Nvidia NVENC)' in text, text
        if '--encode-test-4k-source' in sys.argv:
            source4k = os.path.abspath(sys.argv[sys.argv.index('--encode-test-4k-source')+1])
            scan4k = scan_file(source4k, cli)
            preset4k, _ = detect_preset_from_scan(scan4k)
            assert preset4k == 'Fast 2160p60 4K HEVC', preset4k
            destination4k = os.path.join(os.path.dirname(destination), 'fixture-4k.mp4')
            assert not os.path.exists(destination4k)
            settings4k = dict(settings, src=source4k)
            job4k = dict(src=source4k, dst=destination4k, name='4K fixture', preset=preset4k, detected_preset=preset4k)
            app._transcode_worker(settings4k, [job4k])
            pump()
            text = app.log_text.get('1.0', 'end')
            assert os.path.isfile(destination4k) and os.path.getsize(destination4k) > 0, text
            assert job4k['engine'] == app.engine_var.get(), text
            if app.engine_var.get() == ENGINE_NVIDIA:
                assert 'encavcodecInit: H.265 (Nvidia NVENC)' in text and '-e nvenc_h265_10bit' in text, text
        encoded_test = True
        Path(os.path.dirname(report), 'encoding-log.txt').write_text(text, encoding='utf-8')
    assert not errors, errors
    return dict(ok=True, version=APP_VERSION, frozen=bool(IS_FROZEN),
                pages=list(app.pages), profiles=len(app.presets), animations=True, eta=True, real_encoding=encoded_test, nvidia_encoding=encoded_test and app.engine_var.get() == ENGINE_NVIDIA,
                selected_engine=app.engine_var.get(), nvidia_4k_encoding=encoded_test and app.engine_var.get() == ENGINE_NVIDIA and '--encode-test-4k-source' in sys.argv, errors=errors)


if __name__ == "__main__":
    try:
        app = TranscodeMovieUI()
    except Exception:
        import traceback
        details = traceback.format_exc()
        crash_log = os.path.join(DATA_DIR, 'startup-error.txt')
        try:
            Path(crash_log).write_text(details, encoding='utf-8')
        except OSError:
            pass
        try:
            messagebox.showerror('Library Studio could not start', 'Check that Python includes Tcl/Tk, or use the packaged app for your operating system.\n\nDetails: '+crash_log+'\n\n'+details[-1200:])
        except Exception:
            print(details, file=sys.stderr)
        sys.exit(1)
    if '--self-test-report' in sys.argv:
        report = sys.argv[sys.argv.index('--self-test-report')+1]
        try:
            result = run_interface_self_test(app)
        except Exception:
            import traceback
            result = dict(ok=False, error=traceback.format_exc())
        finally:
            app.destroy()
        write_json_atomic(os.path.abspath(report), result)
        sys.exit(0 if result['ok'] else 1)
    app.mainloop()
