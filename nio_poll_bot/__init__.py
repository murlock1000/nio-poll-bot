import sys

# Check that we're not running on an unsupported Python version.
if sys.version_info < (3, 8):
    print("nio_poll_bot requires Python 3.8 or above.")
    sys.exit(1)

__version__ = "1.0.2"
