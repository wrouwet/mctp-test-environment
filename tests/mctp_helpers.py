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

# MCTP's own transport-layer tag mechanism (msg_tag, 3 bits) serves a
# similar purpose one layer down from inst_id -- confirmed live,
# 2026-08-25: every real response observed so far correctly echoes back
# the request's msg_tag and clears the TO (tag owner) bit (e.g. request
# byte3=0xc8 -> tag_owner=1,msg_tag=0; response byte3=0xc0 ->
# tag_owner=0,msg_tag=0), per DSP0236's convention that a response
# carries the tag its requester allocated, not a new one the responder
# owns. Varied independently from inst_id (separate counter, separate
# spec layer) so this project actually exercises different tag values
# instead of always sending 0 -- otherwise a transport-layer mismatch
# bug could hide behind every request coincidentally using the same tag.
_next_msg_tag = itertools.count()


def next_inst_id():
    return next(_next_inst_id) % 32


def next_msg_tag():
    return next(_next_msg_tag) % 8


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
    msg_tag = next_msg_tag()
    mctp_payload = mctp.build_control_request(
        dest_eid=TARGET_EID,
        src_eid=OUR_EID,
        cmd=cmd,
        data=data,
        inst_id=inst_id,
        msg_tag=msg_tag,
    )
    # The MCTP transport header/payload alone is NOT what goes on the
    # wire -- DSP0237's SMBus block-write wrapper has to precede it (see
    # mctp.build_smbus_block_wrapper()'s docstring for why: an earlier
    # version of this function sent the payload without it, and OpenBIC's
    # mctp_smbus_read() silently discarded every single request as a
    # result, before MCTP-level parsing ever ran).
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, mctp_payload)
    request = wrapper + mctp_payload
    print(f"request bytes (wrapper + MCTP payload): {request.hex(' ')}")
    # WS: the bridge computes and appends a correct PEC byte automatically,
    # covering the wrapper bytes too since they're part of what's handed to it.
    bridge.smbus_write(MCTP_TARGET_ADDR, request)

    for attempt in range(max_drain + 1):
        # Plain listen(), not smbus equivalent -- the bridge's slave-mode
        # capture (I/L) has no PEC awareness of its own (it just captures
        # raw bytes), so PEC verification for a captured response has to
        # happen here, in Python, not on-device. See _verify_and_strip_pec().
        raw = bridge.listen(OUR_I2C_ADDR)
        print(f"response bytes (incl. wrapper + PEC): {raw.hex(' ')}")
        after_pec = _verify_and_strip_pec(raw)
        try:
            resp_src_addr, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
        except ValueError as exc:
            # Same treatment as a stale/mismatched response below: this
            # captured frame isn't a valid answer to anything, but rather
            # than hard-failing immediately, keep listening in case a
            # real response is still coming (mirrors the sibling
            # project's IPMB drain-loop reasoning).
            print(f"discarding malformed SMBus wrapper on captured response ({exc}); "
                  f"still listening...")
            continue
        decoded = mctp.parse_control_response(mctp_response)
        print(f"decoded (from src I2C addr 0x{resp_src_addr:02x}): {decoded}")
        if decoded["cmd"] == cmd and decoded["inst_id"] == inst_id:
            # This IS the real response to our specific request -- Control-
            # layer identity (cmd+inst_id) already confirms that, and a
            # genuinely different/stale response wouldn't coincidentally
            # match both. So a transport-layer tag mismatch here is a real
            # protocol conformance issue worth failing loudly on, not
            # something to silently discard and keep listening for.
            assert decoded["tag_owner"] == 0, (
                f"response's TO (tag owner) bit is set ({decoded['tag_owner']}) -- "
                f"per DSP0236, a response should clear TO to signal it's using the "
                f"tag its requester allocated, not claiming a new one of its own"
            )
            assert decoded["msg_tag"] == msg_tag, (
                f"response echoed msg_tag={decoded['msg_tag']}, expected {msg_tag} "
                f"(the tag this exact request used)"
            )
            return decoded
        print(f"discarding stale response (cmd=0x{decoded['cmd']:02x} "
              f"inst_id={decoded['inst_id']}) that doesn't match ours "
              f"(cmd=0x{cmd:02x} inst_id={inst_id}); still listening...")

    raise AssertionError(
        f"never received a response matching our request (cmd=0x{cmd:02x} "
        f"inst_id={inst_id}) after discarding {max_drain + 1} stale/mismatched/malformed ones"
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
