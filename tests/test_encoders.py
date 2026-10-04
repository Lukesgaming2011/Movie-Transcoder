import os, atexit, tempfile
_test_data=tempfile.TemporaryDirectory();atexit.register(_test_data.cleanup)
os.environ['LIBRARY_STUDIO_DATA_DIR']=_test_data.name
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parents[1] / 'Transcode_Movie_UI.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Encoders(unittest.TestCase):
    def test_real_help_parser(self):
        text = (Path(__file__).parent/'fixtures/handbrake-help.txt').read_text(encoding='utf-8')
        available = m.parse_available_encoders(text)
        self.assertIn('nvenc_h264', available)
        self.assertIn('vce_h265_10bit', available)
        self.assertIn('x265_10bit', available)
        self.assertNotIn('qsv_h264', available)

    def test_each_backend_and_cpu_fallback(self):
        for engine, codes in m.ENCODERS.items():
            available = {'x264', 'x265_10bit', *codes}
            for preset, expected in [('Fast 1080p30', codes[0]), ('Fast 2160p60 4K HEVC', codes[1])]:
                settings = dict(engine=m.ENGINE_AUTO, video='auto_bitrate')
                picked, encoder = m.select_encoder(settings, dict(preset=preset), available)
                self.assertEqual((picked, encoder), (engine, expected))
                job = dict(preset=preset, engine=picked, encoder=encoder)
                args = m.hardware_encoding_args(settings, job)
                self.assertIn(expected, args)
                self.assertEqual('--no-multi-pass' in m.video_encoding_args(settings, job), engine != m.ENGINE_CPU)
        self.assertEqual(m.select_encoder(dict(engine=m.ENGINE_NVIDIA), dict(preset='Fast 1080p30'), {'x264'}), (m.ENGINE_CPU, 'x264'))

    def test_codec_compatibility_and_strict_selection(self):
        job = dict(preset='Fast 2160p60 4K HEVC')
        available = {'nvenc_h264', 'x265_10bit'}
        self.assertEqual(m.select_encoder(dict(engine=m.ENGINE_AUTO), job, available), (m.ENGINE_CPU, 'x265_10bit'))
        with self.assertRaises(RuntimeError):
            m.select_encoder(dict(engine=m.ENGINE_NVIDIA, fallback=False), job, available)

    def test_no_576p_choice(self):
        self.assertFalse(any('576' in p for p in m.DEFAULT_PRESETS))
        self.assertNotIn('Fast 576p25', m.OFFICIAL_PRESETS)
        self.assertEqual(m.DEFAULT_PRESETS['DVD 480p (2,500 kbps)']['preset'], 'Fast 480p30')

    def test_native_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder, 'HandBrakeCLI')
            tool.touch()
            with patch.object(m.os, 'name', 'posix'), patch.object(m, 'read_settings', return_value={}), patch.object(m.shutil, 'which', return_value=str(tool)), patch.object(m.os, 'access', return_value=True):
                self.assertEqual(m.find_tool('HandBrakeCLI'), str(tool))

if __name__ == '__main__':
    unittest.main(verbosity=2)
