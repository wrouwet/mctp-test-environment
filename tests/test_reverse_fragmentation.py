"""Reverse-direction MCTP fragmentation: OpenBIC sending US a multi-packet
response that we need to capture and reassemble on our side.

Uses the peer session's Vendor Defined-PCI (msg type 0x7E) test-only
ECHO command, added specifically because nothing else this platform
implements produces a naturally large enough reply to force
fragmentation the other way. See mctp.build_vendor_pci_echo_request()/
parse_vendor_pci_echo_response() for the exact framing.

CONFIRMED WORKING live, 2026-08-25: both tests below pass, reassembling
a 4-fragment (200-byte) and a 9-fragment (517-byte, including a
pkt_seq wraparound) response correctly, byte-for-byte, on the first
try. This answers the real open question mctp_helpers.
reassemble_response_body() was written with -- whether calling
bridge.listen() repeatedly can keep up with a responder sending
fragments back-to-back -- with a clean yes; no bridge firmware changes
were needed for the receive direction after all.
"""

import mctp
import mctp_helpers
from config import MCTP_TARGET_ADDR, OUR_EID, OUR_I2C_ADDR, TARGET_EID
from mctp import VENDOR_PCI_STATUS_OK


def test_echo_reply_larger_than_mtu_reassembles_correctly(bridge):
    """Request a 200-byte echo reply (comfortably over the 64-byte MTU,
    forcing a real multi-packet response: 4 fragments) and verify the
    reassembled data matches the documented pattern (data[i] == i & 0xFF)
    byte-for-byte, not just checking the length came back right.
    """
    msg_tag = mctp_helpers.next_msg_tag()
    req_len = 200
    request = mctp.build_vendor_pci_echo_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, req_len=req_len, msg_tag=msg_tag
    )
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, request)
    wire_frame = wrapper + request
    print(f"echo request (asking for {req_len} bytes back): {wire_frame.hex(' ')}")
    bridge.smbus_write(MCTP_TARGET_ADDR, wire_frame)

    dest_eid, src_eid, body = mctp_helpers.reassemble_response_body(bridge, msg_tag)
    assert dest_eid == OUR_EID and src_eid == TARGET_EID, (
        f"reassembled response addressed dest_eid=0x{dest_eid:02x} src_eid=0x{src_eid:02x}, "
        f"expected dest_eid=0x{OUR_EID:02x} src_eid=0x{TARGET_EID:02x}"
    )

    decoded = mctp.parse_vendor_pci_echo_response(body)
    print(f"echo response: status=0x{decoded['status']:02x}, {len(decoded['data'])} data bytes")
    assert decoded["status"] == VENDOR_PCI_STATUS_OK, (
        f"expected status OK (0x00) for a {req_len}-byte request (under the 512 cap), "
        f"got 0x{decoded['status']:02x}"
    )
    assert len(decoded["data"]) == req_len, (
        f"expected exactly {req_len} data bytes, got {len(decoded['data'])}"
    )
    expected_pattern = bytes(i & 0xFF for i in range(req_len))
    assert decoded["data"] == expected_pattern, (
        "reassembled data doesn't match the expected i & 0xFF pattern -- byte-level "
        "corruption somewhere in reverse-direction fragmentation/reassembly"
    )
    print(f"reassembled {req_len} bytes across multiple incoming fragments, "
          f"byte-for-byte correct")


def test_echo_reply_over_cap_is_capped(bridge):
    """Request more than the confirmed 512-byte cap and confirm the
    response is capped (status=CAPPED) with capped-length data --
    exercises the cap-handling path specifically, separate from the
    "does reassembly work at all" question the main test above answers.
    """
    msg_tag = mctp_helpers.next_msg_tag()
    req_len = 600  # over the confirmed 512-byte cap
    request = mctp.build_vendor_pci_echo_request(
        dest_eid=TARGET_EID, src_eid=OUR_EID, req_len=req_len, msg_tag=msg_tag
    )
    wrapper = mctp.build_smbus_block_wrapper(OUR_I2C_ADDR, request)
    wire_frame = wrapper + request
    print(f"echo request (asking for {req_len} bytes, over the 512 cap): {wire_frame.hex(' ')}")
    bridge.smbus_write(MCTP_TARGET_ADDR, wire_frame)

    # 512 (capped) data bytes + 5-byte echo header = 517-byte body ->
    # ceil(517/64) = 9 fragments, over reassemble_response_body()'s
    # default max_fragments=8.
    _, _, body = mctp_helpers.reassemble_response_body(bridge, msg_tag, max_fragments=12)
    decoded = mctp.parse_vendor_pci_echo_response(body)
    print(f"echo response: status=0x{decoded['status']:02x}, {len(decoded['data'])} data bytes")
    assert decoded["status"] == mctp.VENDOR_PCI_STATUS_CAPPED, (
        f"expected status CAPPED (0x01) for a {req_len}-byte request (over the 512 cap), "
        f"got 0x{decoded['status']:02x}"
    )
    assert len(decoded["data"]) == mctp.VENDOR_PCI_MAX_LEN, (
        f"expected exactly {mctp.VENDOR_PCI_MAX_LEN} (capped) data bytes, got {len(decoded['data'])}"
    )
    expected_pattern = bytes(i & 0xFF for i in range(mctp.VENDOR_PCI_MAX_LEN))
    assert decoded["data"] == expected_pattern, "capped data doesn't match the expected pattern"
