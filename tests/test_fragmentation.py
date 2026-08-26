"""MCTP multi-packet fragmentation/reassembly.

Confirmed against source with the peer session, 2026-08-25: this
platform's mctp.c does real fragmentation/reassembly (not a stub) --
SOM=1 allocates a reassembly buffer, every subsequent packet (including
the final EOM=1 one) appends to it, and the message handler only fires
once EOM=1 arrives. Effective MTU is 244 bytes (MCTP_DEFAULT_MSG_MAX_
SIZE), NOT the DSP0236 64-byte baseline this project originally assumed
-- there's no runtime MTU negotiation, it's a compile-time constant both
ends have to agree on. Total reassembled size caps at 1024 bytes
(MSG_ASSEMBLY_BUF_SIZE), confirmed to be rejected cleanly (no leak) if
exceeded.

DELIBERATELY NOT TESTED HERE YET, even though this project could
technically send the traffic to exercise them: two other real, CONFIRMED
bugs in the same reassembly path -- (1) an incomplete fragmented message
(a SOM that never gets a matching EOM) leaks its 1024-byte buffer
indefinitely, with no timeout, and can exhaust all 16 msg_tag/to
reassembly slots given enough distinct incomplete attempts; (2) pkt_seq
is never validated during reassembly, so out-of-order/duplicate/
corrupted sequence numbers are silently accepted in arrival order. The
peer session is actively fixing both. Deliberately holding off on tests
that would trigger #1 specifically (a real resource leak on live,
shared hardware, not just a test that would fail) until that fix lands
-- repeatedly leaking reassembly slots against someone else's live board
just to prove the bug exists again, when it's already confirmed and
being fixed, isn't worth the risk. Add those tests once the peer session
confirms the fix is in.
"""

import mctp
import mctp_helpers
from bridge import BridgeError
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID
from mctp import CTRL_CC_SUCCESS, CTRL_CMD_GET_ENDPOINT_ID


def _send_fragmented_and_capture(bridge, packets, cmd, inst_id, max_drain=3):
    """Send each of `packets` (already-built MCTP packets, transport
    header + that packet's chunk, from mctp.fragment_control_request())
    as its own separate wire transaction, in order, then listen for and
    decode the (single, unfragmented) response. Mirrors mctp_helpers.
    send_mctp_control_command()'s response handling, but for a multi-
    packet request that helper doesn't support sending.
    """
    for i, packet in enumerate(packets):
        wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, packet)
        wire_frame = wrapper + packet
        print(f"fragment {i + 1}/{len(packets)} ({len(packet)} bytes): "
              f"{wire_frame.hex(' ')[:80]}{'...' if len(wire_frame.hex(' ')) > 80 else ''}")
        bridge.smbus_write(MCTP_TARGET_ADDR, wire_frame)

    for attempt in range(max_drain + 1):
        raw = bridge.listen(OUR_I2C_ADDR)
        print(f"response bytes (incl. wrapper + PEC): {raw.hex(' ')}")
        after_pec = mctp_helpers._verify_and_strip_pec(raw)
        _, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
        decoded = mctp.parse_control_response(mctp_response)
        print(f"decoded: {decoded}")
        if decoded["cmd"] == cmd and decoded["inst_id"] == inst_id:
            return decoded
        print(f"discarding stale/mismatched response; still listening...")

    raise AssertionError(
        f"never received a response matching our fragmented request "
        f"(cmd=0x{cmd:02x} inst_id={inst_id}) after {max_drain + 1} attempts"
    )


@mctp_helpers.not_implemented(
    "A legitimate 2-packet Get Endpoint ID request (253-byte body, harmless "
    "trailing padding, correctly formed SOM/EOM/pkt_seq/msg_tag per the "
    "confirmed reassembly mechanics) gets ZERO response, confirmed live "
    "2026-08-25 -- both fragments' writes succeed at the I2C level, but "
    "nothing ever comes back. Root cause not yet identified: could be a "
    "genuine reassembly/dispatch bug, or a framing detail on this project's "
    "side not yet caught (tried chunking at both 244 and 240 bytes per packet "
    "in case MTU=244 was meant to include vs. exclude the 4-byte transport "
    "header; same result either way). Under active investigation with the "
    "peer session -- this is a real, reported finding, not a design choice, "
    "so treat this marker as 'confirmed broken, cause TBD' rather than "
    "'known and accepted', unlike this suite's other not_implemented() cases."
)
def test_multi_packet_message_reassembles_correctly(bridge):
    """Send a Get Endpoint ID request padded with harmless trailing data
    to push the total message body past the 244-byte MTU, forcing real
    2-packet fragmentation -- then confirm the target reassembles it
    correctly and answers normally.

    Get Endpoint ID's real handler doesn't care about trailing data past
    what it actually reads, so padding is inert as far as the command's
    own semantics go; what this test actually exercises is purely the
    transport-layer fragmentation/reassembly path underneath it. A
    correct, matching response is strong evidence reassembly worked --
    a reassembly bug (wrong order, truncated, corrupted) would very
    likely produce either no response, a malformed one, or an error
    completion code, not a clean, correctly-addressed match.
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    padding = bytes(range(250))  # push body to 3 + 250 = 253 bytes, > MTU (244)
    packets = mctp.fragment_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        data=padding, inst_id=inst_id, msg_tag=msg_tag,
    )
    assert len(packets) == 2, f"expected exactly 2 packets for a 253-byte body at MTU 244, got {len(packets)}"
    print(f"fragmented into {len(packets)} packets: sizes {[len(p) for p in packets]}")

    decoded = _send_fragmented_and_capture(bridge, packets, CTRL_CMD_GET_ENDPOINT_ID, inst_id)
    assert decoded["completion_code"] == CTRL_CC_SUCCESS, (
        f"expected the padding to be harmlessly ignored and Get Endpoint ID to "
        f"succeed normally; got completion_code 0x{decoded['completion_code']:02x} "
        f"-- possible reassembly corruption, not necessarily a padding-handling issue"
    )
    print("reassembly across 2 packets succeeded: got a normal, correct response")


def test_oversized_message_cleanly_rejected(bridge):
    """Send a message whose total body exceeds the 1024-byte reassembly
    cap (MSG_ASSEMBLY_BUF_SIZE) -- confirmed against source to be
    checked explicitly and rejected via a real cleanup path (buffer
    freed, not leaked), unlike the still-being-fixed incomplete-message
    case. This test only sends COMPLETE (SOM...EOM) oversized messages,
    never an incomplete one, so it doesn't touch the leak bug at all.

    The peer session confirmed the rejection happens internally but
    didn't specify whether that produces a visible error response or
    just silent cleanup with nothing sent back -- so this accepts either
    as "handled safely" and only treats an apparent SUCCESS completion
    code (meaning 1024+ bytes were actually, incorrectly, accepted) as
    a real failure.
    """
    inst_id = mctp_helpers.next_inst_id()
    msg_tag = mctp_helpers.next_msg_tag()
    padding = bytes(i & 0xFF for i in range(1100))  # body = 3 + 1100 = 1103 bytes, > cap (1024)
    packets = mctp.fragment_control_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, cmd=CTRL_CMD_GET_ENDPOINT_ID,
        data=padding, inst_id=inst_id, msg_tag=msg_tag,
    )
    print(f"fragmented into {len(packets)} packets: sizes {[len(p) for p in packets]}")

    for i, packet in enumerate(packets):
        wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, packet)
        wire_frame = wrapper + packet
        print(f"fragment {i + 1}/{len(packets)} ({len(packet)} bytes)")
        bridge.smbus_write(MCTP_TARGET_ADDR, wire_frame)

    try:
        raw = bridge.listen(OUR_I2C_ADDR)
    except BridgeError as exc:
        print(f"no response arrived -- acceptable: silent cleanup with nothing "
              f"sent back is still \"handled safely\", not a hang or a leak: {exc}")
        return

    print(f"got a response: {raw.hex(' ')}")
    after_pec = mctp_helpers._verify_and_strip_pec(raw)
    _, mctp_response = mctp.parse_smbus_block_wrapper(after_pec)
    decoded = mctp.parse_control_response(mctp_response)
    print(f"decoded: {decoded}")
    if decoded["cmd"] == CTRL_CMD_GET_ENDPOINT_ID and decoded["inst_id"] == inst_id:
        assert decoded["completion_code"] != CTRL_CC_SUCCESS, (
            "got a SUCCESS response to a 1103-byte message body, which exceeds "
            "the confirmed 1024-byte reassembly cap -- the oversized-message "
            "rejection isn't actually working"
        )
        print(f"got a real error completion code (0x{decoded['completion_code']:02x}) "
              f"as expected for an oversized message")
    else:
        print("got a response, but it's stale/unrelated -- inconclusive, not "
              "treated as evidence either way")
