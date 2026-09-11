import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.storage import data_file


class ToolStorageTests(unittest.TestCase):
    def test_uses_configured_writable_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"MM_DATA_DIR": temp_dir}):
                path = Path(data_file("shopping_list.json"))

            self.assertEqual(path, Path(temp_dir) / "shopping_list.json")
            self.assertTrue(path.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
