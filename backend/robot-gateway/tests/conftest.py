"""Pytest configuration for robot-gateway tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("GOOGLE_CLIENT_IDS", "test-client-id")
os.environ.setdefault("ALLOWED_USERS", "user@example.com")
