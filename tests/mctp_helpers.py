"""Shared plumbing for every MCTP test in this suite -- the request/
response round trip, PEC verification for slave-mode-captured responses,
and the not_implemented() backlog marker. Mirrors the sibling
openbic-test-environment project's ipmi_helpers.py structure and
conventions deliberately, per the user's explicit ask to build this
project "using same test style/environment".
"""

import itertools

import pytest

import mctp
from config import OUR_EID, OUR_I2C_ADDR, TARGET_EID, MCTP_TARGET_ADDR

# MCTP Control's instance ID field is 5 bits (0-31) and exists for the
# same reason IPMB's seq field does: matching a response to the request
# that produced it, rather than assuming in-order/prompt delivery. A
# fresh one per call, shared across every test file that imports this
# (not per-file), so instance IDs stay unique for the whole session
# regardless of which test files ran before this one.
_next_inst_id = itertools.count()


def next_inst_id():
    return next(_next_inst_id) % 32


def send_mctp_control_command(bridge, cmd, data=b"", max_drain=3):
    """Build an MCTP Control Protocol request with a fresh instance ID,
    send it, and return the decoded response that actually answers it.

    Mirrors the sibling project's send_ipmb_command(): sends once, then
    listens for a response, verifying its SMBus PEC and checking it
    matches (cmd and inst_id) before accepting it -- a stale response to
    an earlier, unrelated request is discarded and we keep listening (up
    to max_drain extra attempts) rather than treating a mismatch as a
    hard failure outright, same reasoning as the sibling project (a
    slow/retried response can arrive later than expected).

    NOT yet verified against real hardware (see config.py's status
    note) -- this is built from confirmed header layouts and the
    confirmed "responder becomes bus master" addressing pattern, but the
    actual round trip (timing, retry behavior, whether OpenBIC's MCTP
    stack behaves the way this assumes under real conditions) is
    unverified until the physical bus is wired up.
    """
    inst_id = next_inst_id()
    request = mctp.build_control_request(
        dest_eid=TARGET_EID,
        src_eid=OUR_EID,
        cmd=cmd,
        data=data,
        inst_id=inst_id,
    )
    print(f"request bytes: {request.hex(' ')}")
    # WS: the bridge computes and appends a correct PEC byte automatically.
    bridge.smbus_write(MCTP_TARGET_ADDR, request)

    for attempt in range(max_drain + 1):
        # Plain listen(), not smbus equivalent -- the bridge's slave-mode
        # capture (I/L) has no PEC awareness of its own (it just captures
        # raw bytes), so PEC verification for a captured response has to
        # happen here, in Python, not on-device. See _verify_and_strip_pec().
        raw = bridge.listen(OUR_I2C_ADDR)
        print(f"response bytes (incl. PEC): {raw.hex(' ')}")
        response = _verify_and_strip_pec(raw)
        decoded = mctp.parse_control_response(response)
        print(f"decoded: {decoded}")
        if decoded["cmd"] == cmd and decoded["inst_id"] == inst_id:
            return decoded
        print(f"discarding stale response (cmd=0x{decoded['cmd']:02x} "
              f"inst_id={decoded['inst_id']}) that doesn't match ours "
              f"(cmd=0x{cmd:02x} inst_id={inst_id}); still listening...")

    raise AssertionError(
        f"never received a response matching our request (cmd=0x{cmd:02x} "
        f"inst_id={inst_id}) after discarding {max_drain + 1} stale/mismatched ones"
    )


def _verify_and_strip_pec(raw):
    """Verify the trailing SMBus PEC byte on a response captured via the
    bridge's slave-mode listen (I/L), and return the payload with it
    stripped off.

    PEC for this direction covers [our_address<<1 | 0 (the responder
    became bus master and WROTE to us, so from the wire's perspective
    it's a write to our address)] followed by the captured data bytes
    (everything except the trailing PEC byte itself) -- computed
    independently here in Python since the bridge firmware's I/L capture
    does no PEC checking of its own (only its WS/RS/XS commands do, and
    those are for transactions the bridge itself initiates as
    controller, not ones where it's the target being written to).
    """
    if len(raw) < 1:
        raise ValueError("response too short to contain a PEC byte at all")
    data, pec_received = raw[:-1], raw[-1]
    pec_expected = mctp.smbus_pec_byte(0, (OUR_I2C_ADDR << 1) | 0)
    pec_expected = mctp.smbus_pec_buf(pec_expected, data)
    if pec_expected != pec_received:
        raise ValueError(
            f"SMBus PEC mismatch on captured response: expected 0x{pec_expected:02x}, "
            f"got 0x{pec_received:02x} -- either a real transmission error, or the "
            f"responder isn't actually appending a correct SMBus PEC"
        )
    return data


def not_implemented(reason):
    """Mark a test as expected to fail because this OpenBIC port doesn't
    implement the command/behavior it exercises yet -- identical
    mechanism and reasoning to the sibling openbic-test-environment
    project's ipmi_helpers.not_implemented(): pytest.mark.xfail(strict=
    True), so the moment real support lands and the test starts
    genuinely passing, the run FAILS loudly (XPASS) instead of quietly
    staying green. Always pass a `reason` explaining what's missing and
    how you know.
    """
    return pytest.mark.xfail(reason=reason, strict=True)
