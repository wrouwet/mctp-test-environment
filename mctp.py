"""MCTP (Management Component Transport Protocol) framing over SMBus/I2C.

Scope is deliberately MCTP transport + MCTP Control Protocol only (per
DSP0236) -- no PLDM or other higher-layer message types. PLDM's header
has its own, different layout (confirmed separately, and NOT the same
shape as MCTP Control's) and is out of scope for this project.

Every byte layout here is confirmed against the actual OpenBIC source
this project tests against (meta-facebook/mcx-n9xx-evk, full-board-port,
via the peer session developing it, 2026-08-25) -- not just transcribed
from the DSP0236 spec from memory. See each function's docstring for
which struct it mirrors.

Wire format for one MCTP-over-SMBus request/response, as this module
builds/parses it (i.e. what actually goes out/comes back over I2C,
*excluding* the physical address+R/W byte that the bridge's own
addressing handles, same convention as the sibling openbic-test-
environment project's ipmb.py):

    [transport header: 4 bytes]
    [msg-type/IC byte: 1 byte]      (part of the control header struct,
    [rq/d/rsvd/inst_id byte: 1 byte] but split out here as separate
    [command code: 1 byte]           fields for clarity)
    [command-specific data...]
    [PEC: 1 byte]                    -- SMBus Packet Error Check, added
                                        automatically by the bridge's
                                        WS/XS commands on the way out;
                                        verified independently in Python
                                        here for bytes captured via the
                                        bridge's I/L slave-mode listen,
                                        which does no PEC checking of
                                        its own (see mctp_helpers.py)
"""

# DSP0237 MCTP-over-SMBus block-write wrapper (mirrors smbus_hdr in
# common/service/mctp/mctp_smbus.c) that precedes the MCTP transport
# header on EVERY frame in both directions -- confirmed against source
# with the peer session, 2026-08-25, after an initial version of this
# module omitted it entirely: the target's mctp_smbus_read() silently
# discards (zero log output) any frame whose first byte isn't this
# wrapper's cmd_code, which is exactly what happened. This is real,
# mandatory framing per the transport binding, not something optional
# or specific to this platform.
SMBUS_MCTP_CMD_CODE = 0x0F

MCTP_HDR_VERSION = 0x01

MSG_TYPE_CONTROL = 0x00
MSG_TYPE_PLDM = 0x01  # not otherwise used in this MCTP-only project

# MCTP Control Protocol command codes (DSP0236), the subset this project
# actually exercises. CTRL_CMD_SET_ENDPOINT_ID/GET_ENDPOINT_ID/
# GET_MESSAGE_TYPE_SUPPORT are confirmed implemented on this OpenBIC
# port; CTRL_CMD_GET_MCTP_VERSION_SUPPORT is confirmed NOT implemented
# (see test_mctp_control.py); CTRL_CMD_GET_ENDPOINT_UUID and
# CTRL_CMD_RESOLVE_ENDPOINT_ID are here to test against the same
# confirmed-empty dispatch table (see test_mctp_control.py) -- their
# command code VALUES are per DSP0236, independent of what this
# platform actually implements.
CTRL_CMD_SET_ENDPOINT_ID = 0x01
CTRL_CMD_GET_ENDPOINT_ID = 0x02
CTRL_CMD_GET_ENDPOINT_UUID = 0x03
CTRL_CMD_GET_MCTP_VERSION_SUPPORT = 0x04
CTRL_CMD_GET_MESSAGE_TYPE_SUPPORT = 0x05
CTRL_CMD_RESOLVE_ENDPOINT_ID = 0x07

# MCTP Control Protocol completion codes (DSP0236).
CTRL_CC_SUCCESS = 0x00
CTRL_CC_ERROR = 0x01
CTRL_CC_ERROR_INVALID_DATA = 0x02
CTRL_CC_ERROR_INVALID_LENGTH = 0x03
CTRL_CC_ERROR_NOT_READY = 0x04
CTRL_CC_ERROR_UNSUPPORTED_CMD = 0x05

# Get Endpoint ID response's third byte (DSP0236's "Endpoint Type" byte,
# _get_eid_resp in mctp_ctrl.h:99-107): [eid_type:2 | rsvd:2 |
# endpoint_type:2 | rsvd:2] -- confirmed against source with the peer
# session, 2026-08-25. eid_type and endpoint_type are each 2-bit fields,
# so bits1-0 and bits5-4 respectively (bitfields declared in this order
# pack LSB-first on this target, same convention already confirmed for
# the transport/control headers).
EID_TYPE_DYNAMIC = 0x00
EID_TYPE_STATIC = 0x01
ENDPOINT_TYPE_SIMPLE = 0x00
ENDPOINT_TYPE_BRIDGE = 0x01


def parse_endpoint_type_byte(b):
    """Decode Get Endpoint ID's third response byte into its eid_type and
    endpoint_type sub-fields (see the constants above)."""
    return {
        "eid_type": b & 0x3,
        "endpoint_type": (b >> 4) & 0x3,
    }


def smbus_pec_byte(crc, b):
    """One step of the SMBus PEC CRC-8 (poly 0x07, MSB-first, no
    reflection, init 0) -- identical algorithm to the bridge firmware's
    own smbus_pec_byte() in usb_main.c, verified independently against
    the published CRC-8/SMBUS standard check value (0xF4 for
    "123456789") when that firmware feature was built."""
    crc ^= b
    for _ in range(8):
        crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def smbus_pec_buf(crc, data):
    for b in data:
        crc = smbus_pec_byte(crc, b)
    return crc


def build_smbus_block_wrapper(src_i2c_addr, mctp_payload):
    """Build the 3-byte DSP0237 MCTP-over-SMBus block-write wrapper that
    must precede the MCTP transport header on the wire: [cmd_code=0x0F]
    [byte_count] [src_addr]. Mirrors `smbus_hdr` in
    common/service/mctp/mctp_smbus.c.

    byte_count = 1 (for src_addr) + len(mctp_payload), per the standard
    SMBus Block Write convention (count of bytes following the count
    byte itself, i.e. src_addr plus the actual MCTP payload). src_addr
    is the REQUESTER's own 7-bit I2C address, left-shifted by 1 with
    bit0 set.

    The caller is responsible for concatenating this wrapper's bytes
    immediately before `mctp_payload` and sending the result as one
    write (see mctp_helpers.send_mctp_control_command()) -- this
    function only builds the wrapper itself.
    """
    byte_count = (1 + len(mctp_payload)) & 0xFF
    src_addr_byte = ((src_i2c_addr << 1) | 1) & 0xFF
    return bytes([SMBUS_MCTP_CMD_CODE, byte_count, src_addr_byte])


def parse_smbus_block_wrapper(raw):
    """Inverse of build_smbus_block_wrapper(): validate and strip the
    3-byte wrapper from a captured frame. Returns (src_i2c_addr,
    mctp_payload). Raises ValueError if the wrapper's cmd_code doesn't
    match or byte_count doesn't match the actual remaining length --
    the same check OpenBIC's own mctp_smbus_read() does, except that
    function fails *silently* on the target side (no log at all, which
    is exactly what made the original missing-wrapper bug hard to spot
    without checking the target's console directly) -- a Python caller
    should actually find out about a malformed wrapper via an exception,
    not silence.
    """
    if len(raw) < 3:
        raise ValueError(f"too short to contain the SMBus block wrapper: {len(raw)} bytes")
    cmd_code, byte_count, src_addr_byte = raw[0], raw[1], raw[2]
    if cmd_code != SMBUS_MCTP_CMD_CODE:
        raise ValueError(
            f"wrapper cmd_code 0x{cmd_code:02x} != MCTP's 0x{SMBUS_MCTP_CMD_CODE:02x} "
            f"-- this isn't an MCTP-over-SMBus frame at all"
        )
    payload = raw[3:]
    expected_payload_len = byte_count - 1  # byte_count includes src_addr itself
    if len(payload) != expected_payload_len:
        raise ValueError(
            f"wrapper byte_count (0x{byte_count:02x}) says {expected_payload_len} payload "
            f"bytes should follow src_addr, but {len(payload)} actually did"
        )
    src_i2c_addr = (src_addr_byte >> 1) & 0x7F
    return src_i2c_addr, bytes(payload)


def build_transport_header(dest_eid, src_eid, msg_tag=0, tag_owner=1, som=1, eom=1, pkt_seq=0):
    """Build the 4-byte MCTP transport header.

    Mirrors `mctp_hdr` (common/service/mctp/mctp.c:29-43) exactly:
    byte0 hdr_ver=0x01, byte1 dest_ep, byte2 src_ep, byte3 =
    som(bit7)|eom(bit6)|pkt_seq(bits5-4)|to(bit3)|msg_tag(bits2-0).

    Defaults produce a single-packet, unfragmented message (som=1,
    eom=1, pkt_seq=0) from the requester (tag_owner=1, meaning "I
    originated this tag") -- what every test in this project sends
    unless deliberately testing fragmentation.
    """
    byte3 = (
        ((som & 0x1) << 7)
        | ((eom & 0x1) << 6)
        | ((pkt_seq & 0x3) << 4)
        | ((tag_owner & 0x1) << 3)
        | (msg_tag & 0x7)
    )
    return bytes([MCTP_HDR_VERSION, dest_eid & 0xFF, src_eid & 0xFF, byte3])


def parse_transport_header(data):
    """Inverse of build_transport_header(). Returns a dict; raises
    ValueError if too short or hdr_ver doesn't match."""
    if len(data) < 4:
        raise ValueError(f"MCTP transport header too short: {len(data)} bytes")
    hdr_ver, dest_eid, src_eid, byte3 = data[:4]
    if hdr_ver != MCTP_HDR_VERSION:
        raise ValueError(f"unexpected MCTP header version 0x{hdr_ver:02x} (expected 0x{MCTP_HDR_VERSION:02x})")
    return {
        "dest_eid": dest_eid,
        "src_eid": src_eid,
        "som": (byte3 >> 7) & 0x1,
        "eom": (byte3 >> 6) & 0x1,
        "pkt_seq": (byte3 >> 4) & 0x3,
        "tag_owner": (byte3 >> 3) & 0x1,
        "msg_tag": byte3 & 0x7,
    }


def build_control_request(dest_eid, src_eid, cmd, data=b"", inst_id=0, msg_tag=0):
    """Build a full MCTP Control Protocol request: transport header +
    control header + command data (no PEC -- add that via the bridge's
    smbus_write()/smbus_write_read(), which computes and appends it).

    Mirrors `mctp_ctrl_hdr` (common/service/mctp/mctp_ctrl.h:109-124):
    byte0 = msg-type/IC (ic=0, msg_type=MSG_TYPE_CONTROL), byte1 =
    rq(bit7)|d(bit6)|rsvd(bit5)|inst_id(bits4-0) with rq=1 (this is a
    request) and d=0 (not a datagram), byte2 = cmd.
    """
    transport = build_transport_header(dest_eid, src_eid, msg_tag=msg_tag)
    msg_type_ic_byte = MSG_TYPE_CONTROL & 0x7F  # ic=0
    rq_d_inst_byte = (1 << 7) | (0 << 6) | (inst_id & 0x1F)  # rq=1, d=0
    return transport + bytes([msg_type_ic_byte, rq_d_inst_byte, cmd]) + bytes(data)


def parse_control_response(payload):
    """Parse a captured MCTP Control Protocol response (transport header
    + control header + completion code + data). Does NOT verify PEC --
    the payload passed in should already have had its trailing PEC byte
    verified and stripped by the caller (see mctp_helpers.py, which does
    this for bytes captured via the bridge's I/L slave-mode listen).

    Raises ValueError if too short, msg_type isn't Control, or the
    response bit (rq) is set (meaning this is actually a request, not a
    response -- would indicate a framing/direction mistake somewhere).
    """
    if len(payload) < 7:
        raise ValueError(f"MCTP Control response too short: {len(payload)} bytes")

    transport = parse_transport_header(payload)
    msg_type_ic_byte, rq_d_inst_byte, cmd, completion_code = payload[4:8]

    msg_type = msg_type_ic_byte & 0x7F
    ic = (msg_type_ic_byte >> 7) & 0x1
    if msg_type != MSG_TYPE_CONTROL:
        raise ValueError(f"expected MCTP Control (msg_type=0x00), got msg_type=0x{msg_type:02x}")

    rq = (rq_d_inst_byte >> 7) & 0x1
    d = (rq_d_inst_byte >> 6) & 0x1
    inst_id = rq_d_inst_byte & 0x1F
    if rq != 0:
        raise ValueError("rq bit is set on what should be a response (looks like a request, not a response)")

    data = payload[8:]
    return {
        **transport,
        "ic": ic,
        "d": d,
        "inst_id": inst_id,
        "cmd": cmd,
        "completion_code": completion_code,
        "data": bytes(data),
    }
