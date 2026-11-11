"""Catalog metadata: plugin manifest and skill front matter."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_supported_manifest_and_compatibility(self):
        text = (ROOT / 'plugin.yaml').read_text()
        self.assertIn('\nname: defuzz-componentize\n', text)
        self.assertIn('\nauthor: defuzzr\n', text)
        self.assertNotIn('\nprovides_commands:', text)
        self.assertNotIn('\nconfig:', text)
        self.assertIn('requires_hermes: ">=0.23.0"', text)
        self.assertIn('config_schema:', text)
        self.assertIn('default: 900', text)
        skill_path = ROOT / 'skills/defuzz-componentize/SKILL.md'
        self.assertTrue(skill_path.is_file())
        skill_text = skill_path.read_text()
        self.assertIn('\nname: defuzz-componentize\n', skill_text)
        self.assertIn('\nauthor: defuzzr\n', skill_text)
        import re
        desc_match = re.search(r'^description:\s*"([^"]+)"\s*$', skill_text, re.MULTILINE)
        assert desc_match is not None
        desc = desc_match.group(1)
        self.assertLessEqual(len(desc), 60)
        self.assertRegex(desc, r'^Use when .+\.\s+.+\.$')

    def test_license(self):
        license_text = (ROOT / 'LICENSE').read_text()
        self.assertIn('MIT License', license_text)
        self.assertIn('Copyright (c) 2026 defuzzr', license_text)


if __name__ == '__main__':
    unittest.main()
