"""Validate the kit sources and keep the README's disclosure summary small."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KitSourcesAndReadmeTests(unittest.TestCase):
    def test_kit_json_loads_with_unique_names(self):
        paths = sorted((ROOT / 'vendor/kits').glob('*/kit.json'))
        self.assertTrue(paths)
        names = []
        for path in paths:
            with self.subTest(path=path):
                kit = json.loads(path.read_text(encoding='utf-8'))
                self.assertIsInstance(kit, dict)
                self.assertEqual(kit['id'], path.parent.name)
                names.append(kit['id'])
        self.assertEqual(len(names), len(set(names)))

    def test_readme_is_lean_and_has_disclosure_labels(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertLessEqual(len(text.splitlines()), 70)
        labels = set(re.findall(r'\*\*([^:*]+):\*\*', text, re.MULTILINE))
        self.assertTrue({
            'Reads', 'Writes', 'Runs', 'Network', 'Background processes',
            'Credentials and telemetry',
        }.issubset(labels))


if __name__ == '__main__':
    unittest.main()
