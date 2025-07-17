#!/usr/bin/env python3
from pathlib import Path
base_dir = Path(os.getenv(
    "PLANICA_PATH",
    str(Path.home() / "my_apps" / "planica")
))
print(base_dir)
