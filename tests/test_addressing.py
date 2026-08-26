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


def _send_get_endpoint_id_and_report(bridge, dest_eid, label):
    """Shared plumbing for the dest_eid addressing tests below: send a
    well-formed, correctly-PEC'd Get Endpoint ID request to `dest_eid`,
    and return (got_matching_response, decoded_or_None) rather than
    asserting anything itself -- each caller decides what a match or a
    timeout actually means for its specific dest_eid.
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    mctp_payload = mctp.build_control_request(
        dest_eid=dest_eid, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        inst_id=inst_id, msg_tag=msg_tag,
    )
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, mctp_payload)
    request = wrapper + mctp_payload
    print(f"request bytes ({label}, dest_eid=0x{dest_eid:02x}): {request.hex(' ')}")
    bridge.smbus_write(MCTP_TARGET_ADDR, request)

    try:
        raw = bridge.listen(OUR_I2C_ADDR)
    except BridgeError as exc:
        print(f"no response arrived ({exc})")
        return False, None

    print(f"got a response: {raw.hex(' ')}")
    after_pec = mctp_helpers._verify_and_strip_pec(raw)
    _, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
    decoded = mctp.parse_control_response(mctp_response)
    print(f"decoded: {decoded}")
    matches = decoded["cmd"] == CTRL_CMD_GET_ENDPOINT_ID and decoded["inst_id"] == inst_id
    if not matches:
        print("(doesn't match this request -- stale from something else, inconclusive)")
    return matches, (decoded if matches else None)


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
    matched, _ = _send_get_endpoint_id_and_report(bridge, WRONG_EID, "wrong EID")
    assert not matched, (
        f"the endpoint responded to a request addressed to dest_eid=0x{WRONG_EID:02x}, "
        f"not its own EID (0x{TARGET_EID:02x}) -- dest_eid enforcement appears to have "
        f"regressed"
    )


def test_request_to_broadcast_eid(bridge):
    """Send a Get Endpoint ID request addressed to the broadcast EID
    (0xFF) instead of TARGET_EID.

    Genuinely unconfirmed territory, unlike the wrong-EID case above:
    DSP0236 has real, legitimate uses for broadcast addressing (e.g.
    Endpoint Discovery), but whether a general Control command like Get
    Endpoint ID sent to the broadcast EID should be processed by an
    already-assigned endpoint isn't something this project has a
    confirmed answer for. Observing and reporting rather than asserting
    either outcome -- this is a "let's find out" test, not a
    conformance check with a known-correct answer yet.
    """
    matched, decoded = _send_get_endpoint_id_and_report(bridge, 0xFF, "broadcast EID")
    if matched:
        print("the endpoint responds to Get Endpoint ID sent to the broadcast EID -- "
              "noted, not asserted as right or wrong without a confirmed spec answer "
              "for this specific case")
    else:
        print("the endpoint does not respond to Get Endpoint ID sent to the broadcast "
              "EID -- also noted, same caveat")


def test_request_to_null_eid(bridge):
    """Send a Get Endpoint ID request addressed to the null EID (0x00)
    instead of TARGET_EID.

    Same "let's find out" framing as the broadcast-EID test above: the
    null EID has a real, specific meaning in MCTP (an endpoint that
    hasn't been assigned a real EID yet listens here for discovery), but
    this endpoint already has a real, assigned EID (0x09) -- whether it
    also still answers at the null EID isn't confirmed either way.
    """
    matched, decoded = _send_get_endpoint_id_and_report(bridge, 0x00, "null EID")
    if matched:
        print("the endpoint responds to Get Endpoint ID sent to the null EID even "
              "though it already has a real assigned EID -- noted, not asserted as "
              "right or wrong without a confirmed spec answer for this specific case")
    else:
        print("the endpoint does not respond to Get Endpoint ID sent to the null EID "
              "-- also noted, same caveat")
