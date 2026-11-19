"""fork(coco-ww): smoke tests for code ported from coco-ww that targets ok-script 3.0.5b7 while we run 3.0.6."""
import inspect
import unittest
from unittest.mock import Mock

from ok.task.task import BaseTask, ExecutorOperation, FindFeature

from src.char.BaseChar import BaseChar
from src.combat.FKeyStart import FStartCombatTask
from src.recorder.AxisRecorder import AxisRecorderTask, _missing_patch_points
from src.recorder.recorder import Recorder
from src.task.BaseCombatTask import BaseCombatTask
from src.task.BaseWWTask import BaseWWTask


class TestForkCocoRecorder(unittest.TestCase):

    def test_every_recorder_patch_point_exists(self):
        self.assertEqual(_missing_patch_points(), [])

    def test_recorder_uninstall_restores_the_original_methods(self):
        targets = [(FindFeature, 'find_one'), (FindFeature, 'find_feature'),
                   (ExecutorOperation, 'calculate_color_percentage'), (BaseCombatTask, 'get_cd'),
                   (BaseCombatTask, 'get_current_con'), (BaseWWTask, 'in_team'), (BaseChar, 'perform')]
        originals = {target: target[0].__dict__[target[1]] for target in targets}
        recorder = Recorder(plugin_version='test')
        try:
            recorder.install()
            self.assertTrue(any(target[0].__dict__[target[1]] is not original
                                for target, original in originals.items()))
        finally:
            recorder.uninstall()
        for (owner, name), original in originals.items():
            self.assertIs(owner.__dict__[name], original, f'{owner.__name__}.{name}')

    def test_ported_tasks_build_on_this_framework(self):
        executor = Mock(debug=False)
        recorder_task = AxisRecorderTask(executor, None)
        self.assertTrue(recorder_task.default_config['_enabled'])  # coco default: records auto combat runs
        shell = FStartCombatTask(executor, None)
        self.assertFalse(shell.visible)

    def test_held_input_overrides_match_framework_signatures(self):
        # BaseCombatTask wraps these to remember held keys; written against 3.0.5b7
        for name in ('send_key_down', 'send_key_up', 'mouse_down', 'mouse_up'):
            ours = inspect.signature(getattr(BaseCombatTask, name))
            base = inspect.signature(getattr(BaseTask, name))
            self.assertEqual(list(ours.parameters), list(base.parameters), name)


if __name__ == '__main__':
    unittest.main()
