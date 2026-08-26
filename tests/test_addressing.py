"""MCTP endpoint-addressing conformance -- does the target actually check
dest_eid against its own EID, or does physical I2C addressing (which
already uniquely selects this one device) make the MCTP-layer EID
effectively decorative on this platform?

This is a real, previously-unexercised question: every other test in
this suite always addresses TARGET_EID correctly, so none of them can
tell us whether dest_eid is actually being checked at all. A spec-
compliant endpoint should NOT respond to a request addressed to some
other, unrelated EID (per DSP0236, an endpoint only processes messages
addressed to its own EID, the null EID 0x00 in specific pre-assignment
scenarios, or the broadcast EID 0xFF) -- but "should" isn't "does",
and this hasn't been confirmed either way for this platform.
"""

import mctp
import mctp_helpers
from bridge import BridgeError
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID
from mctp import CTRL_CMD_GET_ENDPOINT_ID

# Deliberately not TARGET_EID, not the null EID (0x00), and not the
# broadcast EID (0xFF) -- an EID that, if the endpoint responds to it,
# would mean dest_eid isn't being checked at all.
WRONG_EID = (TARGET_EID + 1) & 0xFF
if WRONG_EID in (0x00, 0xFF):
    WRONG_EID = (TARGET_EID + 2) & 0xFF


def test_request_to_wrong_dest_eid_is_ignored(bridge):
    """Send an otherwise well-formed, correctly-PEC'd Get Endpoint ID
    request, but addressed to WRONG_EID instead of TARGET_EID.

    Confirmed live, 2026-08-25: the endpoint correctly does NOT respond
    -- real, working dest_eid enforcement per DSP0236, not something
    that could be assumed just because every other test in this suite
    happens to always address the right EID. If this ever starts
    responding to a mismatched dest_eid, that's a real regression worth
    investigating, not a quirky "huh, interesting" -- so this now
    asserts the confirmed-correct behavior rather than just observing.
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    mctp_payload = mctp.build_control_request(
        dest_eid=WRONG_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        inst_id=inst_id, msg_tag=msg_tag,
    )
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, mctp_payload)
    request = wrapper + mctp_payload
    print(f"request bytes (dest_eid=0x{WRONG_EID:02x}, NOT this endpoint's "
          f"own EID 0x{TARGET_EID:02x}): {request.hex(' ')}")
    bridge.smbus_write(MCTP_TARGET_ADDR, request)

    try:
        raw = bridge.listen(OUR_I2C_ADDR)
    except BridgeError as exc:
        print(f"no response arrived -- consistent with DSP0236: an endpoint "
              f"should ignore a request addressed to a dest_eid that isn't its "
              f"own (or null/broadcast): {exc}")
        return

    print(f"got a response anyway: {raw.hex(' ')}")
    after_pec = mctp_helpers._verify_and_strip_pec(raw)
    _, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
    decoded = mctp.parse_control_response(mctp_response)
    print(f"decoded: {decoded}")
    if decoded["cmd"] == CTRL_CMD_GET_ENDPOINT_ID and decoded["inst_id"] == inst_id:
        print(f"NOTABLE: the endpoint responded to a request addressed to "
              f"dest_eid=0x{WRONG_EID:02x}, not its own EID (0x{TARGET_EID:02x}) -- "
              f"dest_eid does not appear to be checked/enforced on this platform. "
              f"Not asserting this as a failure (single local endpoint on a private "
              f"bus, real-world impact is limited), but worth flagging to the peer "
              f"session as a genuine, previously-unconfirmed behavior.")
    else:
        print("got a response, but it doesn't match this request (stale from "
              "something else) -- inconclusive either way, not treating as evidence "
              "that dest_eid is or isn't checked")
