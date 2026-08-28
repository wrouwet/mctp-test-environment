"""Framing-layer edge cases that aren't specific to any one MCTP Control
command.

NOT yet run against real hardware -- see config.py's status note. This
one is additionally speculative in a second way: unlike the sibling
ipmi-test-environment project's identically-shaped checksum test
(confirmed against source before being written), nobody has confirmed
from source what OpenBIC's MCTP-over-SMBus stack actually does with a
bad PEC -- reject the I2C transaction outright (NAK, if PEC is checked
in hardware/at the driver level before the message ever reaches MCTP
code), or silently drop the message after accepting the write (mirroring
IPMB's checksum-failure behavior). This test is written to tolerate
either -- and to correctly fail only on the one outcome that would
actually be alarming: getting back a well-formed response that matches
this deliberately-corrupted request.
"""

import mctp
import mctp_helpers
from bridge import BridgeError
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID
from mctp import CTRL_CMD_GET_ENDPOINT_ID


def test_corrupted_pec_does_not_produce_a_matching_response(bridge):
    """Build a well-formed Get Endpoint ID request, deliberately corrupt
    its SMBus PEC byte, and send it via plain write() (not smbus_write(),
    which would just compute a correct PEC for whatever we hand it --
    defeating the point).

    Whatever actually happens on the wire (an outright NAK from write()
    itself, silence, or an unrelated stale response), the one thing that
    must NOT happen is a well-formed response matching this exact
    request's cmd+inst_id -- that would mean the corruption was accepted
    and processed as if it were valid.
    """
    inst_id = mctp_helpers.next_inst_id()
    mctp_payload = mctp.build_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID, inst_id=inst_id
    )
    # Must include the DSP0237 SMBus block-write wrapper, same as
    # send_mctp_control_command() -- otherwise this isn't testing "PEC
    # corruption", it's just re-triggering the missing-wrapper bug (a
    # different, already-fixed problem) instead.
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, mctp_payload)
    frame = wrapper + mctp_payload
    correct_pec = mctp.smbus_pec_byte(0, (MCTP_TARGET_ADDR << 1) | 0)
    correct_pec = mctp.smbus_pec_buf(correct_pec, frame)
    corrupted_pec = correct_pec ^ 0xFF
    request = frame + bytes([corrupted_pec])
    print(f"request bytes (deliberately corrupted PEC): {request.hex(' ')}")

    try:
        bridge.write(MCTP_TARGET_ADDR, request)
    except BridgeError as exc:
        print(f"write itself was rejected (e.g. NAK) -- one acceptable outcome: {exc}")
        return

    try:
        raw = bridge.listen(OUR_I2C_ADDR)
    except BridgeError as exc:
        print(f"no response arrived -- another acceptable outcome: {exc}")
        return

    print(f"got a response anyway: {raw.hex(' ')}")
    try:
        after_pec = mctp_helpers._verify_and_strip_pec(raw)
        _, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
        decoded = mctp.parse_control_response(mctp_response)
    except ValueError as exc:
        print(f"response was malformed/failed its own PEC or wrapper check -- acceptable, "
              f"not a real answer to our corrupted request: {exc}")
        return

    assert not (decoded["cmd"] == CTRL_CMD_GET_ENDPOINT_ID and decoded["inst_id"] == inst_id), (
        "got a well-formed, PEC-valid response matching our deliberately-corrupted "
        "request -- the corruption was accepted and processed as if valid"
    )
    print("that response doesn't match this request's cmd+inst_id, so it's an unrelated "
          "stale response from something else -- still consistent with our corrupted "
          "request having been rejected")
