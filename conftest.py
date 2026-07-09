import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import simant._env  # noqa: E402,F401  (puts the win16_re submodule, and via it dos_re, on sys.path)
