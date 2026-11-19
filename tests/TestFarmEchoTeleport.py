import unittest
from unittest.mock import Mock, PropertyMock, call, patch

from src.task import BossBook
from src.task.FarmEchoTask import FarmEchoTask
from src.task.WWOneTimeTask import WWOneTimeTask


# fork(coco-ww): the F2 list is searched by boss name (BossBook) instead of row index + hardcoded totals,
# and entering retries the challenge buttons (click_challenge_until_in_world); see test_boss_book.py
# and test_challenge_entry.py for those pieces. These tests pin the order FarmEchoTask drives them in.
def make_task(config, is_team, lang='zh_CN'):
    task = FarmEchoTask.__new__(FarmEchoTask)
    task.config = config
    task.info = {}
    for name in ('ensure_main', 'info_set', 'openF2Book', 'open_boss_book', 'click', 'click_configured_boss_level',
                 'click_challenge_until_in_world', 'wait_click_travel', 'wait_in_team_and_world', 'sleep'):
        setattr(task, name, Mock())
    task.click_book_target_by_name = Mock(side_effect=lambda category, target, serial: target or f'#{serial}')
    task.wait_after_book_click = Mock(return_value=is_team)
    patcher = patch.object(FarmEchoTask, 'game_lang', new_callable=PropertyMock, return_value=lang)
    patcher.start()
    return task, patcher


class TestFarmEchoTeleport(unittest.TestCase):

    def make_task(self, config, is_team, lang='zh_CN'):
        task, patcher = make_task(config, is_team, lang)
        self.addCleanup(patcher.stop)
        return task

    def test_special_nightmare_is_found_by_name(self):
        name = BossBook.known_options('mengyan')[2]
        task = self.make_task({'Teleport to Boss': 'Special Nightmare', 'Special Nightmare Name': name},
                              is_team=True)

        self.assertTrue(task.teleport_to_configured_boss())

        task.open_boss_book.assert_called_once_with('mengyan')
        task.click_book_target_by_name.assert_called_once_with('mengyan', BossBook.realm_of(name), 1)
        task.click_challenge_until_in_world.assert_called_once_with(time_out=120)
        task.click_configured_boss_level.assert_not_called()

    def test_non_chinese_client_counts_rows_by_serial(self):
        task = self.make_task({'Teleport to Boss': 'Boss Challenge', 'Boss Challenge Name': 'whatever',
                               'Which Boss Challenge to Teleport': 3}, is_team=False, lang='en_US')

        self.assertFalse(task.teleport_to_configured_boss())

        task.open_boss_book.assert_called_once_with('qiangdi')
        task.click_book_target_by_name.assert_called_once_with('qiangdi', '', 3)
        task.wait_click_travel.assert_called_once_with()
        task.wait_in_team_and_world.assert_called_once_with(time_out=120)

    def test_weekly_challenge_picks_level_then_enters(self):
        name = BossBook.known_options('zhange')[1]
        task = self.make_task({'Teleport to Boss': 'Weekly Challenge', 'Weekly Boss Name': name}, is_team=True)
        steps = Mock()
        for step in ('click_configured_boss_level', 'click_challenge_until_in_world', 'wait_click_travel'):
            steps.attach_mock(getattr(task, step), step)

        self.assertTrue(task.teleport_to_configured_boss())

        self.assertEqual([call.click_configured_boss_level(), call.click_challenge_until_in_world(time_out=120)],
                         steps.mock_calls)

    def test_reentry_from_f_picks_level_then_enters(self):
        task = self.make_task({}, is_team=True)
        task.wait_until = Mock(return_value=True)
        task.send_key = Mock()
        task.init_parameters = Mock()
        steps = Mock()
        for step in ('send_key', 'click_configured_boss_level', 'click_challenge_until_in_world'):
            steps.attach_mock(getattr(task, step), step)

        task.enter_configured_boss_realm_from_f()

        self.assertEqual([call.send_key('f', after_sleep=3), call.click_configured_boss_level(),
                          call.click_challenge_until_in_world(time_out=120)], steps.mock_calls)

    def test_popup_mistaken_for_claim_is_retried_at_most_three_times(self):
        task = self.make_task({}, is_team=True)
        task.do_run = Mock(side_effect=RuntimeError('Teleport to boss failed'))
        task.handle_claim_button = Mock(return_value=True)
        task.handle_monthly_card = Mock(return_value=False)

        with patch.object(WWOneTimeTask, 'run'), self.assertRaises(RuntimeError):
            task.run()

        self.assertEqual(4, task.do_run.call_count)

    def test_farm_continues_after_claim_popup_is_closed(self):
        task = self.make_task({}, is_team=True)
        task.do_run = Mock(side_effect=[RuntimeError('Teleport to boss failed'), None])
        task.handle_claim_button = Mock(return_value=True)
        task.handle_monthly_card = Mock(return_value=False)

        with patch.object(WWOneTimeTask, 'run'):
            task.run()

        self.assertEqual(2, task.do_run.call_count)


if __name__ == '__main__':
    unittest.main()
