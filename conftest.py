import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from bridge import I2CBridge


@pytest.fixture(scope="session")
def bridge():
    """A connection to the FRDM-MCXA153 USB-to-I2C bridge, shared across tests."""
    b = I2CBridge()
    yield b
    b.close()


@pytest.fixture(autouse=True)
def _bus_settle():
    """Let the shared LPI2C target drain between tests. Since the
    2026-08-27 bus consolidation, MCTP (0x10) and IPMB (0x20) are two
    addresses on one target instance with no clock-stretch backpressure;
    a heavy test (fragmentation/reassembly) can leave the bus busy into
    the next test's first transaction. A short settle before each test
    removes the resulting transient NAK / listen-timeout noise. Runs
    sequentially -- do not parallelise these suites across the one bus.
    """
    time.sleep(0.05)
    yield
