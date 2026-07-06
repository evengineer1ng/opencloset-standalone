"""Test harness config for Oracle Radio.

Reference organs (forkuniverse, neikos, oracle, ftb) are *pre-stocked plugins* — under
the club model they live in plugins/organs/ and are loaded on demand. Until they're
vendored in, any test that instantiates a REAL organ raises ModuleNotFoundError for the
organ's module. We convert that into a SKIP, which is exactly the bouncer's behavior for
a capability the club hasn't installed yet: not a failure, just "not present in this club."

Engine, contract, lens, binding, club, evidence, index, observation and all synthetic
tests run unaffected; only the real-organ integration tests skip when the organ is absent.
"""
import sys
import os

import pytest

# Make the repo root importable so `from tests.test_oradio_engine import ...` (shared
# synthetic helpers) resolves regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(__file__))

# Pre-stocked reference organs live in plugins/organs/. Putting that dir on the path
# is what the club's bouncer does when it "installs" an organ — so a shim's bare
# `import oracle_kingdom` resolves to the vendored plugin. Organs not yet vendored
# there still raise ModuleNotFoundError and skip (see the hook below).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "plugins", "organs"))

# Module roots that mean "a real organ plugin isn't installed in this club."
_ORGAN_MODULES = {"forkuniverse", "neikos", "oracle_kingdom", "plugins", "ftb_game"}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        return (yield)
    except ModuleNotFoundError as exc:
        root = (exc.name or "").split(".")[0]
        if root in _ORGAN_MODULES:
            pytest.skip(f"organ plugin '{root}' not installed in this club (pre-stock pending)")
        raise
    except Exception as exc:
        # the loader now wraps a missing organ's ImportError as MissingPlugin — treat the
        # same: a pre-stock-pending organ skips, anything else propagates.
        if type(exc).__name__ == "MissingPlugin":
            cause = getattr(exc, "__cause__", None)
            root = (getattr(cause, "name", "") or "").split(".")[0]
            if root in _ORGAN_MODULES:
                pytest.skip(f"organ '{getattr(exc, 'kind', '?')}' not installed (pre-stock pending)")
        raise
