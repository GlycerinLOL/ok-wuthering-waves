"""Fork guard: fails after an upstream merge that drops the Character Code tab."""
import importlib
import importlib.util
import unittest
from pathlib import Path

from config import config


class TestForkCharacterCodeTab(unittest.TestCase):
    def test_tab_registered_in_config(self):
        self.assertIn(["src.gui.CharacterCodeTab", "CharacterCodeTab"], config.get('custom_tabs', []))

    def test_tab_module_imports(self):
        from ok.ui.qt.widget.CustomTab import CustomTab
        module = importlib.import_module("src.gui.CharacterCodeTab")
        self.assertTrue(issubclass(module.CharacterCodeTab, CustomTab))

    def test_ok_script_still_loads_custom_tabs(self):
        source = Path(importlib.util.find_spec("ok.ui.qt.MainWindow").origin).read_text(encoding='utf-8')
        self.assertIn("custom_tabs", source)


if __name__ == '__main__':
    unittest.main()
