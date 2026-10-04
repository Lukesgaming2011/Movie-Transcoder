import importlib.util
import pathlib
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('motion_studio', pathlib.Path(__file__).resolve().parents[1] / 'Transcode_Movie_UI.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Owner:
    def __init__(self):
        self.callbacks = {}
        self.next = 0
        self.reduce_motion = types.SimpleNamespace(get=lambda: False)

    def after(self, delay, callback):
        self.next += 1
        self.callbacks[self.next] = callback
        return self.next

    def after_cancel(self, timer):
        self.callbacks.pop(timer, None)

    def winfo_ismapped(self):
        return True

    def frame(self):
        callbacks = list(self.callbacks.values())
        self.callbacks.clear()
        for callback in callbacks:
            callback()


class Motion(unittest.TestCase):
    def test_interrupt_preserves_displayed_value_and_one_frame_timer(self):
        owner = Owner()
        motion = m.Motion(owner)
        now = [0.]
        values, old_finishes = [], []
        with patch.object(m.time, 'perf_counter', side_effect=lambda: now[0]):
            motion.tween('progress', 0, 50, values.append, duration=1, finish=lambda: old_finishes.append(True))
            motion.tween('navigation', 0, 80, lambda value: None, duration=1)
            self.assertEqual(len(owner.callbacks), 1)
            now[0] = .5
            owner.frame()
            displayed = values[-1]
            self.assertEqual(displayed, 25)
            motion.tween('progress', displayed, 100, values.append, duration=1)
            self.assertEqual(values[-1], displayed)
            self.assertFalse(old_finishes)
            self.assertEqual(len(owner.callbacks), 1)
            now[0] = 2
            owner.frame()
            self.assertEqual(values[-1], 100)
            self.assertFalse(motion.running)
            self.assertFalse(owner.callbacks)

    def test_reduced_motion_and_settle_leave_no_pending_frames(self):
        owner = Owner()
        motion = m.Motion(owner)
        values, finishes = [], []
        motion.tween('page', 0, 100, values.append, finish=lambda: finishes.append(True))
        motion.settle()
        self.assertEqual(values[-1], 100)
        self.assertEqual(finishes, [True])
        self.assertFalse(owner.callbacks)
        owner.reduce_motion = types.SimpleNamespace(get=lambda: True)
        motion.tween('page', 0, 10, values.append)
        self.assertEqual(values[-1], 10)
        self.assertFalse(motion.running)


if __name__ == '__main__':
    unittest.main()
