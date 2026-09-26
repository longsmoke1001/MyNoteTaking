import os
import sys

# Make the `src` package importable when pytest runs from the repository root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
