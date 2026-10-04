import importlib.util
import os
import pathlib
import tempfile
import unittest


class ETA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory()
        cls.previous = os.environ.get('LIBRARY_STUDIO_DATA_DIR')
        os.environ['LIBRARY_STUDIO_DATA_DIR'] = cls.data.name
        spec = importlib.util.spec_from_file_location('eta_studio', pathlib.Path(__file__).resolve().parents[1] / 'Transcode_Movie_UI.py')
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop('LIBRARY_STUDIO_DATA_DIR', None)
        else:
            os.environ['LIBRARY_STUDIO_DATA_DIR'] = cls.previous
        cls.data.cleanup()

    def test_multipass_does_not_finish_halfway(self):
        p = self.m.encoding_progress
        self.assertEqual(p('Encoding: task 1 of 2, 100.00 % (60 fps)'), 50)
        self.assertEqual(p('Encoding: task 2 of 2, 0.00 %'), 50)
        self.assertEqual(p('Encoding: task 2 of 2, 50.00 %'), 75)
        self.assertEqual(p('Encoding: task 1 of 1, 50.00 %'), 50)
        self.assertIsNone(p('Scanning title 1'))

    def test_queue_uses_sizes_and_omits_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            jobs = []
            for n, size in enumerate([100, 300, 1000]):
                source = root / f'{n}.mkv'
                source.write_bytes(b'x' * size)
                jobs.append(dict(src=str(source), dst=str(root / f'{n}.mp4')))
            pathlib.Path(jobs[2]['dst']).write_bytes(b'existing')
            now = [0]
            eta = self.m.ConversionETA(jobs, clock=lambda: now[0])
            self.assertIn('Calculating', eta.start(0))
            now[0] = 30
            # Half of the 100-byte file took 30s: 30s current + 180s pending.
            text = eta.text(50)
            self.assertIn('File: ~30s', text)
            self.assertIn('Queue: ~3m 30s', text)
            eta.complete(0)
            eta.start(1)
            now[0] = 60
            self.assertIn('Queue: ~30s', eta.text(50))

    def test_retry_discards_previous_encoder_speed(self):
        now = [0]
        eta = self.m.ConversionETA([dict(src='absent', dst='absent-output')], clock=lambda: now[0])
        eta.start(0)
        now[0] = 10
        self.assertIn('~10s', eta.text(50))
        self.assertIn('Calculating', eta.start(0))
        now[0] = 50
        self.assertIn('~40s', eta.text(50))
        self.assertIn('Finalizing', eta.text(100))


if __name__ == '__main__':
    unittest.main()
