import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")
"""Test configuration and fixtures."""
import os
import sys

# Ensure VCBlogger root is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
