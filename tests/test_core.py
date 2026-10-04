import importlib.util, unittest, tempfile, pathlib, queue, types, os, atexit
_test_data=tempfile.TemporaryDirectory();atexit.register(_test_data.cleanup)
os.environ['LIBRARY_STUDIO_DATA_DIR']=_test_data.name
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('studio',str(pathlib.Path(__file__).resolve().parents[1]/'Transcode_Movie_UI.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.temp.name)
        self.logs=[];self.done=[]
        self.app=types.SimpleNamespace(cancel_requested=False,current_process=None,ui_queue=queue.Queue(),log=lambda msg,tag='INFO':self.logs.append((tag,msg)),finish_movie=self.done.append,progress_bar=types.SimpleNamespace(configure=lambda **kw:None))
        self.app.animate_progress=lambda bar,value,**kw:bar.configure(value=value)
        self.app.lbl_task_status=types.SimpleNamespace(configure=lambda **kw:None)
        self.app.check_cancel=types.MethodType(m.TranscodeMovieUI.check_cancel,self.app)
        self.app._encode_job=types.MethodType(m.TranscodeMovieUI._encode_job,self.app)
        self.settings=dict(src=str(self.root/'source'),dst=str(self.root/'out'),cli='handbrake.exe',preset='auto',name='',year='',extras='Skip Extras (main movie only)',min_size=0,dedup=True,include_sub=False,online_year=False,video='Automatic bitrate (DVD 2500 / HD 19000 kbps)',bitrate='19000',rf='20',audio='Preset Audio Default',stereo='192',surround='512',scratch=False)
    def tearDown(self): self.temp.cleanup()
    def drain(self):
        while not self.app.ui_queue.empty():self.app.ui_queue.get()()
    def test_auto_and_explicit_profiles(self):
        for preset,bitrate in [('DVD','2500'),('Blue-ray basic','19000'),('Fast 2160p60 4K HEVC','40000')]:
            self.assertEqual(m.video_encoding_args(self.settings,{'preset':preset}),['-b',bitrate,'--multi-pass'])
        for name in m.HANDBRAKE_PROFILES:self.assertEqual(m.DEFAULT_PRESETS[name]['video_mode'],'preset_default')
        self.settings.update(video='Custom Target Bitrate (kbps)',bitrate='23000')
        self.assertEqual(m.video_encoding_args(self.settings,{'preset':'DVD'}),['-b','23000','--multi-pass'])
        self.settings.update(video='Custom Constant Quality (RF / CQ)',rf='18')
        self.assertEqual(m.video_encoding_args(self.settings,{}),['-q','18'])
    def test_nvidia_encoder_selection_and_passes(self):
        self.settings['engine']=m.ENGINE_NVIDIA
        for preset,encoder in [('DVD','nvenc_h264'),('Fast 1080p30','nvenc_h264'),('Fast 2160p60 4K HEVC','nvenc_h265_10bit'),('HQ 2160p60 4K HEVC Surround','nvenc_h265_10bit')]:
            args=m.hardware_encoding_args(self.settings,{'preset':preset})
            self.assertEqual(args[args.index('-e')+1],encoder)
            self.assertIn('nvdec',args)
            rate=m.video_encoding_args(self.settings,{'preset':preset})
            self.assertNotIn('--multi-pass',rate)
            self.assertIn('--no-multi-pass',rate)
        self.settings['video']='original_copy'
        self.assertEqual(m.hardware_encoding_args(self.settings,{'preset':'DVD'}),[])
        self.settings.update(video='auto_bitrate',engine=m.ENGINE_CPU)
        self.assertIn('x264',m.hardware_encoding_args(self.settings,{'preset':'DVD'}))
        self.assertIn('--multi-pass',m.video_encoding_args(self.settings,{'preset':'DVD'}))

    def scan(self,copy=False):
        source=self.root/'source';source.mkdir()
        for title in ['DVD (2001)','HD (2002)','UHD (2003)']:
            folder=source/title;folder.mkdir();(folder/(title+'.mkv')).write_bytes(b'original')
        if copy:self.settings['video']='original_copy'
        def fake_scan(path,*args):
            size='720x480' if pathlib.Path(path).name.startswith('DVD') else '1920x1080' if pathlib.Path(path).name.startswith('HD') else '3840x2160'
            return '+ size: '+size+'\n'
        with patch.object(m,'scan_file',side_effect=fake_scan):return m.TranscodeMovieUI.scan_movies(self.app,self.settings)
    def test_mixed_queue_resolution_and_rates(self):
        jobs=self.scan();byname={j['name'].split(' ')[0]:j for j in jobs}
        self.assertIn('2,500 kbps',byname['DVD']['info']);self.assertIn('19,000 kbps',byname['HD']['info']);self.assertIn('40,000 kbps',byname['UHD']['info'])
        self.assertEqual(byname['UHD']['preset'],'Fast 2160p60 4K HEVC')
        self.assertTrue(all(j['dst'].endswith('.mp4') for j in jobs))
    def test_original_copy_queue_paths(self):
        jobs=self.scan(copy=True);self.assertTrue(all(j['dst'].endswith('.mkv') for j in jobs))
    def test_encoding_worker_uses_resolved_bitrate(self):
        src=self.root/'source.mkv';src.write_bytes(b'movie');dst=self.root/'out/movie.mp4';commands=[]
        class FakeProcess:
            def __init__(self,command,**kw):
                commands.append(command);pathlib.Path(command[command.index('-o')+1]).write_bytes(b'encoded');self.stdout=[];self.returncode=0
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self):return 0
        job=dict(src=str(src),dst=str(dst),name='Movie',preset='Blue-ray basic')
        with patch.object(m.subprocess,'Popen',FakeProcess):m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job])
        self.drain();self.assertEqual(dst.read_bytes(),b'encoded');self.assertEqual(commands[0][commands[0].index('-b')+1],'19000');self.assertIn('--multi-pass',commands[0]);self.assertIn('1 saved',self.done[-1])
    def test_zero_exit_without_output_keeps_handbrake_error(self):
        src=self.root/'source.mkv';src.write_bytes(b'movie');dst=self.root/'out/movie.mp4'
        class FakeProcess:
            def __init__(self,*args,**kw):self.stdout=['unknown option (--bad-option)\n'];self.returncode=0
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self):return 0
        job=dict(src=str(src),dst=str(dst),name='Movie',preset='Blue-ray basic')
        with patch.object(m.subprocess,'Popen',FakeProcess):m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job])
        self.drain()
        self.assertFalse(dst.exists())
        self.assertTrue(any(tag=='ERROR' and 'unknown option (--bad-option)' in message for tag,message in self.logs))
        self.assertIn('1 failed',self.done[-1])

    def test_copy_preserves_bytes_and_skips_existing(self):
        src=self.root/'source.mkv';src.write_bytes(bytes(range(256))*23000);dst=self.root/'out/movie.mkv';self.settings['video']='original_copy'
        job=dict(src=str(src),dst=str(dst),name='Movie',preset='Blue-ray basic')
        self.app._update_progress_ui=lambda *args:None
        with patch.object(m.subprocess,'Popen',side_effect=AssertionError('Copy must not encode')):
            m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job]);self.drain()
            self.assertEqual(src.read_bytes(),dst.read_bytes())
            src.write_bytes(b'changed');m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job]);self.drain()
            self.assertIn('1 skipped',self.done[-1]);self.assertNotEqual(src.read_bytes(),dst.read_bytes())
    def test_cancel_copy_removes_partial_output(self):
        src=self.root/'source.mkv';src.write_bytes(b'x'*5000000);dst=self.root/'out/movie.mkv';self.settings['video']='original_copy'
        job=dict(src=str(src),dst=str(dst),name='Movie',preset='DVD');count=[0]
        self.app._update_progress_ui=lambda *args:None
        def cancel():
            count[0]+=1
            if count[0]>=3:self.app.cancel_requested=True;raise InterruptedError('Cancelled')
        self.app.check_cancel=cancel
        m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job]);self.drain()
        self.assertFalse(dst.exists());self.assertFalse(list(dst.parent.glob('.library-encode-*')));self.assertIn('Cancelled',self.done[-1])
    def test_gpu_failure_falls_back_once_and_preserves_queue(self):
        self.settings.update(engine=m.ENGINE_AUTO, fallback=True)
        source=self.root/'source.mkv';source.write_bytes(b'movie')
        jobs=[dict(src=str(source), dst=str(self.root/f'out/movie{n}.mp4'), name=f'Movie {n}', preset='Blue-ray basic') for n in range(2)]
        commands=[]
        class Process:
            def __init__(self, command, **kw):
                commands.append(command)
                gpu='nvenc_h264' in command
                self.returncode=1 if gpu else 0
                self.stdout=['Cannot initialize NVIDIA encoder\n'] if gpu else ['Encoding: task 1 of 1, 100.0 %\n']
                if not gpu:Path=pathlib.Path;Path(command[command.index('-o')+1]).write_bytes(b'encoded')
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self):return self.returncode
        self.app._update_progress_ui=lambda *args:None
        with patch.object(m,'probe_encoders',return_value={'nvenc_h264','x264'}),patch.object(m.subprocess,'Popen',Process):
            m.TranscodeMovieUI._transcode_worker(self.app,self.settings,jobs)
        self.drain()
        self.assertEqual(sum('nvenc_h264' in c for c in commands),1)
        self.assertEqual(sum('x264' in c for c in commands),2)
        self.assertIn('2 saved',self.done[-1])
        self.assertTrue(all(pathlib.Path(j['dst']).read_bytes()==b'encoded' for j in jobs))
        self.assertTrue(any('Retrying' in text for _,text in self.logs))
    def test_cancelled_gpu_does_not_retry_or_publish(self):
        self.settings.update(engine=m.ENGINE_AUTO, fallback=True)
        source=self.root/'source.mkv';source.write_bytes(b'movie')
        job=dict(src=str(source),dst=str(self.root/'out/movie.mp4'),name='Movie',preset='Blue-ray basic')
        commands=[];app=self.app
        class Process:
            def __init__(self, command, **kw):commands.append(command);self.returncode=1;self.stdout=[]
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self):app.cancel_requested=True;return 1
        with patch.object(m,'probe_encoders',return_value={'nvenc_h264','x264'}),patch.object(m.subprocess,'Popen',Process):
            m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job])
        self.drain();self.assertEqual(len(commands),1);self.assertFalse(pathlib.Path(job['dst']).exists());self.assertIn('Cancelled',self.done[-1])
    def test_failed_started_encode_is_not_restarted(self):
        self.settings.update(engine=m.ENGINE_AUTO, fallback=True)
        source=self.root/'source.mkv';source.write_bytes(b'movie')
        job=dict(src=str(source),dst=str(self.root/'out/movie.mp4'),name='Movie',preset='Blue-ray basic')
        commands=[]
        class Process:
            def __init__(self, command, **kw):commands.append(command);self.returncode=1;self.stdout=['Encoding: task 1 of 1, 58.0 %\n','Unreadable input\n']
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self):return 1
        self.app._update_progress_ui=lambda *args:None
        with patch.object(m,'probe_encoders',return_value={'nvenc_h264','x264'}),patch.object(m.subprocess,'Popen',Process):
            m.TranscodeMovieUI._transcode_worker(self.app,self.settings,[job])
        self.drain();self.assertEqual(len(commands),1);self.assertFalse(pathlib.Path(job['dst']).exists());self.assertIn('1 failed',self.done[-1])
if __name__=='__main__':unittest.main(verbosity=2)

