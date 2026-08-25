"""Foundational test: is the MCTP endpoint present on the bus at all.

Everything else in this suite assumes this passes first. NOT yet run
against real hardware -- see config.py's status note.
"""

from config import MCTP_TARGET_ADDR


def test_detect_mctp_endpoint(bridge):
    """The MCTP endpoint should respond on the I2C bus at MCTP_TARGET_ADDR."""
    addrs = bridge.scan()
    print(f"bus scan found: {[hex(a) for a in addrs]}")
    assert MCTP_TARGET_ADDR in addrs, (
        f"MCTP endpoint not found at 0x{MCTP_TARGET_ADDR:02x}; "
        f"devices found: {[hex(a) for a in addrs]}"
    )
