from types import SimpleNamespace
from unittest.mock import patch

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.Labels import Labels
from src.char import CharFactory
from src.char.CharFactory import get_char_by_pos
from src.char.Jiyan import Jiyan
from src.char.Lily import Lily
from src.task.AutoCombatTask import AutoCombatTask


class TestForkCharMisread(TaskTestCase):
    """fork: a confirmed char whose portrait is dimmed (switch cooldown) must not be replaced by a weak read."""
    task_class = AutoCombatTask
    config = {**config, 'template_matching': dict(config['template_matching'])}

    def read(self, old, best_name, best_confidence):
        best = SimpleNamespace(name=best_name, confidence=best_confidence)
        with patch.object(CharFactory, '_find_registered_char', return_value=None), \
                patch.object(self.task, 'find_best_match_in_box', return_value=best):
            return get_char_by_pos(self.task, None, 2, old)

    def test_dimmed_lily_is_kept_over_weak_jiyan(self):
        old = Lily(self.task, 2, char_name=Labels.char_lily, confidence=0.95)
        self.assertIs(self.read(old, Labels.char_jiyan, 0.65), old)

    def test_clear_portrait_of_a_new_char_still_replaces(self):
        old = Lily(self.task, 2, char_name=Labels.char_lily, confidence=0.95)
        self.assertIsInstance(self.read(old, Labels.char_jiyan, 0.85), Jiyan)

    def test_unconfirmed_old_char_is_not_protected(self):
        old = Lily(self.task, 2, char_name=Labels.char_lily, confidence=0.80)
        self.assertIsInstance(self.read(old, Labels.char_jiyan, 0.65), Jiyan)


if __name__ == '__main__':
    import unittest
    unittest.main()
