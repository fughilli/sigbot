import sys

import pytest


def test_imports():
    import anthropic  # noqa: F401
    import apscheduler  # noqa: F401
    import httpx  # noqa: F401
    import yaml  # noqa: F401


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
