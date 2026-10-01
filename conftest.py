"""pytest root: make the package importable without installation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
