"""Entry point of the packaged executables (see omos.spec)."""
import sys

from omos.desktop import entry

sys.exit(entry())
