-- DART v1 Wireshark dissector
-- Decodes DART application-layer packets carried over UDP port 9999.

if set_plugin_info then
    set_plugin_info({
        version = "1.0.0",
        author = "DART Project",
        description = "DART v1 (Deadline-Aware Reliable Telemetry) dissector",
    })
end

local DART_PORT = 9999
local HEADER_LENGTH = 40
local DART_MAGIC = "DART"
local DART_VERSION = 1

local message_types = {
    [1] = "REGISTER_REQ",
    [2] = "REGISTER_RES",
    [3] = "DATA_BATCH",
    [4] = "LATEST_UPDATE",
    [5] = "CRITICAL_ALERT",
    [6] = "ACK",
    [7] = "CONFIG_REQ",
    [8] = "CONFIG_RES",
    [9] = "HEARTBEAT",
    [10] = "HEARTBEAT_ACK",
    [11] = "ERROR",
}

local delivery_modes = {
    [0] = "CONTROL",
    [1] = "BEST_EFFORT_BATCH",
    [2] = "LATEST_ONLY",
    [3] = "CRITICAL_RELIABLE",
}

local metric_ids = {
    [1] = "TEMPERATURE_C",
    [2] = "HUMIDITY_PERCENT",
    [3] = "SMOKE_PPM",
    [4] = "POSITION_X",
    [5] = "POSITION_Y",
    [6] = "BATTERY_PERCENT",
}

local status_codes = {
    [0] = "NONE",
    [200] = "OK",
    [201] = "REGISTERED",
    [202] = "ACCEPTED",
    [204] = "NO_CONTENT",
    [400] = "MALFORMED",
    [401] = "UNREGISTERED",
    [408] = "EXPIRED",
    [409] = "DUPLICATE",
    [413] = "PAYLOAD_TOO_LARGE",
    [422] = "INVALID_PAYLOAD",
    [429] = "RATE_LIMITED",
    [500] = "INTERNAL_ERROR",
    [503] = "BUSY",
}

local dart = Proto("dart", "DART v1 Telemetry Protocol")

local fields = {
    magic = ProtoField.string("dart.magic", "Magic"),
    version = ProtoField.uint8("dart.version", "Version", base.DEC),
    message_type = ProtoField.uint8(
        "dart.msg_type", "Message type", base.DEC, message_types
    ),
    delivery = ProtoField.uint8(
        "dart.delivery", "Delivery mode", base.DEC, delivery_modes
    ),
    flags = ProtoField.uint8("dart.flags", "Flags", base.HEX),
    flag_ack_required = ProtoField.bool(
        "dart.flags.ack_required", "ACK required", 8, nil, 0x01
    ),
    flag_retransmission = ProtoField.bool(
        "dart.flags.retransmission", "Retransmission", 8, nil, 0x02
    ),
    flag_simulated = ProtoField.bool(
        "dart.flags.simulated", "Simulated sensor", 8, nil, 0x04
    ),
    session_id = ProtoField.uint32("dart.session_id", "Session ID", base.DEC),
    sensor_id = ProtoField.uint32("dart.sensor_id", "Sensor ID", base.DEC),
    sequence = ProtoField.uint32("dart.sequence", "Sequence", base.DEC),
    timestamp_ms = ProtoField.uint64(
        "dart.timestamp_ms", "Timestamp (Unix ms)", base.DEC
    ),
    ttl_ms = ProtoField.uint32("dart.ttl_ms", "Time to live (ms)", base.DEC),
    payload_length = ProtoField.uint16(
        "dart.payload_length", "Payload length", base.DEC
    ),
    status_code = ProtoField.uint16(
        "dart.status_code", "Status code", base.DEC, status_codes
    ),
    checksum = ProtoField.uint32("dart.checksum", "CRC-32", base.HEX),
    checksum_computed = ProtoField.uint32(
        "dart.checksum.computed", "Computed CRC-32", base.HEX
    ),
    checksum_valid = ProtoField.bool(
        "dart.checksum.valid", "CRC-32 valid"
    ),
    payload = ProtoField.bytes("dart.payload", "Payload"),
    payload_text = ProtoField.string("dart.payload.text", "UTF-8 text"),
    payload_json = ProtoField.string("dart.payload.json", "JSON text"),
    trailing = ProtoField.bytes("dart.trailing", "Trailing bytes"),
    batch_count = ProtoField.uint16(
        "dart.batch.count", "Metric count", base.DEC
    ),
    batch_record = ProtoField.bytes("dart.batch.record", "Metric record"),
    metric_id = ProtoField.uint8(
        "dart.metric_id", "Metric ID", base.DEC, metric_ids
    ),
    metric_value = ProtoField.float("dart.metric_value", "Metric value"),
    metric_age_ms = ProtoField.uint16(
        "dart.metric_age_ms", "Metric age (ms)", base.DEC
    ),
}

dart.fields = fields

local experts = {
    short_packet = ProtoExpert.new(
        "dart.expert.short_packet",
        "Packet is shorter than the DART header",
        expert.group.MALFORMED,
        expert.severity.ERROR
    ),
    bad_magic = ProtoExpert.new(
        "dart.expert.bad_magic",
        "Invalid DART magic",
        expert.group.MALFORMED,
        expert.severity.ERROR
    ),
    unsupported_version = ProtoExpert.new(
        "dart.expert.unsupported_version",
        "Unsupported DART version",
        expert.group.PROTOCOL,
        expert.severity.WARN
    ),
    unknown_type = ProtoExpert.new(
        "dart.expert.unknown_type",
        "Unknown DART message type",
        expert.group.PROTOCOL,
        expert.severity.WARN
    ),
    unknown_delivery = ProtoExpert.new(
        "dart.expert.unknown_delivery",
        "Unknown DART delivery mode",
        expert.group.PROTOCOL,
        expert.severity.WARN
    ),
    unknown_flags = ProtoExpert.new(
        "dart.expert.unknown_flags",
        "Unknown DART flag bits are set",
        expert.group.PROTOCOL,
        expert.severity.WARN
    ),
    bad_length = ProtoExpert.new(
        "dart.expert.bad_length",
        "Payload length does not match the UDP payload",
        expert.group.MALFORMED,
        expert.severity.ERROR
    ),
    invalid_payload = ProtoExpert.new(
        "dart.expert.invalid_payload",
        "Message-specific payload is malformed",
        expert.group.MALFORMED,
        expert.severity.ERROR
    ),
    checksum_mismatch = ProtoExpert.new(
        "dart.expert.checksum_mismatch",
        "DART CRC-32 does not match",
        expert.group.CHECKSUM,
        expert.severity.ERROR
    ),
}

dart.experts = experts

-- Prefer Wireshark's bit library when present. The arithmetic fallback keeps the
-- dissector usable with Lua builds that expose neither bit nor bit32.
local function arithmetic_bxor32(a, b)
    local result = 0
    local place = 1
    for _ = 1, 32 do
        local a_bit = a % 2
        local b_bit = b % 2
        if a_bit ~= b_bit then
            result = result + place
        end
        a = math.floor(a / 2)
        b = math.floor(b / 2)
        place = place * 2
    end
    return result
end

local bxor32
if bit32 and bit32.bxor then
    bxor32 = function(a, b)
        return bit32.bxor(a, b)
    end
elseif bit and bit.bxor then
    bxor32 = function(a, b)
        local result = bit.bxor(a, b)
        if result < 0 then
            return result + 4294967296
        end
        return result
    end
else
    bxor32 = arithmetic_bxor32
end

local crc32_table = {}
for index = 0, 255 do
    local value = index
    for _ = 1, 8 do
        if value % 2 == 1 then
            value = bxor32(math.floor(value / 2), 0xEDB88320)
        else
            value = math.floor(value / 2)
        end
    end
    crc32_table[index] = value
end

-- IEEE/zlib CRC-32 over the header and declared payload. The checksum field
-- itself (header bytes 36..39) is treated as four zero bytes.
local function calculate_crc32(buffer, packet_length)
    local crc = 0xFFFFFFFF
    for offset = 0, packet_length - 1 do
        local octet = 0
        if offset < 36 or offset > 39 then
            octet = buffer(offset, 1):uint()
        end
        local table_index = bxor32(crc % 256, octet)
        crc = bxor32(math.floor(crc / 256), crc32_table[table_index])
    end
    return bxor32(crc, 0xFFFFFFFF)
end

local function bit_is_set(value, bit_number)
    return math.floor(value / (2 ^ bit_number)) % 2 == 1
end

local function looks_like_json(text)
    local first_character = string.match(text, "^%s*(.)")
    return first_character == "{" or first_character == "["
end

local function decode_text_or_json(payload_range, payload_tree, pinfo)
    local payload_text = payload_range:string()
    if looks_like_json(payload_text) then
        payload_tree:add(fields.payload_json, payload_range)

        -- Also hand likely JSON to Wireshark's built-in JSON dissector when it
        -- is available. The protected call prevents optional JSON support from
        -- breaking DART decoding.
        local json_dissector = Dissector.get("json")
        if json_dissector then
            pcall(function()
                json_dissector:call(payload_range:tvb(), pinfo, payload_tree)
            end)
            pinfo.cols.protocol = "DART"
        end
    else
        payload_tree:add(fields.payload_text, payload_range)
    end
end

local function decode_data_batch(payload_range, payload_tree)
    local length = payload_range:len()
    if length < 2 then
        payload_tree:add_proto_expert_info(
            experts.invalid_payload,
            "DATA_BATCH requires a 2-byte metric count"
        )
        return
    end

    local metric_count = payload_range(0, 2):uint()
    payload_tree:add(fields.batch_count, payload_range(0, 2))

    local required_length = 2 + metric_count * 7
    if length ~= required_length then
        payload_tree:add_proto_expert_info(
            experts.invalid_payload,
            string.format(
                "DATA_BATCH count %u requires %u payload bytes, but %u are available",
                metric_count,
                required_length,
                length
            )
        )
    end

    local cursor = 2
    for record_number = 1, metric_count do
        if cursor + 7 > length then
            break
        end

        local record_range = payload_range(cursor, 7)
        local record_tree = payload_tree:add(
            fields.batch_record,
            record_range,
            string.format("Metric record %u", record_number)
        )
        record_tree:add(fields.metric_id, payload_range(cursor, 1))
        record_tree:add(fields.metric_value, payload_range(cursor + 1, 4))
        record_tree:add(fields.metric_age_ms, payload_range(cursor + 5, 2))
        cursor = cursor + 7
    end
end

local function decode_latest_update(payload_range, payload_tree)
    local length = payload_range:len()
    if length ~= 5 then
        payload_tree:add_proto_expert_info(
            experts.invalid_payload,
            string.format(
                "LATEST_UPDATE requires exactly 5 payload bytes, but %u are available",
                length
            )
        )
        return
    end

    payload_tree:add(fields.metric_id, payload_range(0, 1))
    payload_tree:add(fields.metric_value, payload_range(1, 4))
end

function dart.dissector(buffer, pinfo, tree)
    local captured_length = buffer:len()
    pinfo.cols.protocol = "DART"

    if captured_length < HEADER_LENGTH then
        pinfo.cols.info = string.format(
            "Malformed DART packet (%u/%u header bytes)",
            captured_length,
            HEADER_LENGTH
        )
        local short_tree = tree:add(dart, buffer(), "DART (truncated header)")
        short_tree:add_proto_expert_info(
            experts.short_packet,
            string.format(
                "DART needs a %u-byte header; only %u bytes were captured",
                HEADER_LENGTH,
                captured_length
            )
        )
        return captured_length
    end

    local magic = buffer(0, 4):string()
    local version = buffer(4, 1):uint()
    local message_type = buffer(5, 1):uint()
    local delivery = buffer(6, 1):uint()
    local flags = buffer(7, 1):uint()
    local sensor_id = buffer(12, 4):uint()
    local sequence = buffer(16, 4):uint()
    local declared_payload_length = buffer(32, 2):uint()
    local status_code = buffer(34, 2):uint()
    local transmitted_checksum = buffer(36, 4):uint()

    local message_name = message_types[message_type]
        or string.format("UNKNOWN_TYPE_%u", message_type)
    local info = string.format(
        "%s  sensor=%u  seq=%u  payload=%u B",
        message_name,
        sensor_id,
        sequence,
        declared_payload_length
    )
    if bit_is_set(flags, 1) then
        info = info .. "  [RETRANSMISSION]"
    end
    if status_code ~= 0 then
        info = info .. string.format(
            "  status=%s",
            status_codes[status_code] or tostring(status_code)
        )
    end
    pinfo.cols.info = info

    local packet_tree = tree:add(
        dart,
        buffer(),
        string.format("DART v%u: %s", version, message_name)
    )
    local header_tree = packet_tree:add(
        dart,
        buffer(0, HEADER_LENGTH),
        "Header (40 bytes)"
    )

    local magic_item = header_tree:add(fields.magic, buffer(0, 4))
    local version_item = header_tree:add(fields.version, buffer(4, 1))
    local type_item = header_tree:add(fields.message_type, buffer(5, 1))
    local delivery_item = header_tree:add(fields.delivery, buffer(6, 1))
    local flags_item = header_tree:add(fields.flags, buffer(7, 1))
    flags_item:add(fields.flag_ack_required, buffer(7, 1))
    flags_item:add(fields.flag_retransmission, buffer(7, 1))
    flags_item:add(fields.flag_simulated, buffer(7, 1))
    header_tree:add(fields.session_id, buffer(8, 4))
    header_tree:add(fields.sensor_id, buffer(12, 4))
    header_tree:add(fields.sequence, buffer(16, 4))
    header_tree:add(fields.timestamp_ms, buffer(20, 8))
    header_tree:add(fields.ttl_ms, buffer(28, 4))
    local length_item = header_tree:add(fields.payload_length, buffer(32, 2))
    header_tree:add(fields.status_code, buffer(34, 2))
    local checksum_item = header_tree:add(fields.checksum, buffer(36, 4))

    if magic ~= DART_MAGIC then
        magic_item:add_proto_expert_info(
            experts.bad_magic,
            string.format("Expected magic %q, found %q", DART_MAGIC, magic)
        )
    end
    if version ~= DART_VERSION then
        version_item:add_proto_expert_info(
            experts.unsupported_version,
            string.format(
                "This dissector supports DART version %u, packet declares version %u",
                DART_VERSION,
                version
            )
        )
    end
    if not message_types[message_type] then
        type_item:add_proto_expert_info(
            experts.unknown_type,
            string.format("Message type %u is not defined by DART v1", message_type)
        )
    end
    if not delivery_modes[delivery] then
        delivery_item:add_proto_expert_info(
            experts.unknown_delivery,
            string.format("Delivery mode %u is not defined by DART v1", delivery)
        )
    end
    if flags >= 8 then
        flags_item:add_proto_expert_info(
            experts.unknown_flags,
            string.format("Flags contain undefined bits: 0x%02X", flags)
        )
    end

    local actual_payload_length = captured_length - HEADER_LENGTH
    local expected_packet_length = HEADER_LENGTH + declared_payload_length
    local available_payload_length = math.min(
        actual_payload_length,
        declared_payload_length
    )

    if declared_payload_length ~= actual_payload_length then
        length_item:add_proto_expert_info(
            experts.bad_length,
            string.format(
                "Header declares %u payload bytes, UDP payload contains %u bytes after the header",
                declared_payload_length,
                actual_payload_length
            )
        )
    end

    if captured_length >= expected_packet_length then
        local computed_checksum = calculate_crc32(buffer, expected_packet_length)
        local computed_item = checksum_item:add(
            fields.checksum_computed,
            computed_checksum
        )
        computed_item:set_generated()
        local checksum_is_valid = computed_checksum == transmitted_checksum
        local valid_item = checksum_item:add(
            fields.checksum_valid,
            checksum_is_valid
        )
        valid_item:set_generated()
        if not checksum_is_valid then
            checksum_item:add_proto_expert_info(
                experts.checksum_mismatch,
                string.format(
                    "Transmitted 0x%08X, computed 0x%08X",
                    transmitted_checksum,
                    computed_checksum
                )
            )
        end
    end

    if available_payload_length > 0 then
        local payload_range = buffer(HEADER_LENGTH, available_payload_length)
        local payload_tree = packet_tree:add(fields.payload, payload_range)
        if message_type == 3 then
            decode_data_batch(payload_range, payload_tree)
        elseif message_type == 4 then
            decode_latest_update(payload_range, payload_tree)
        else
            decode_text_or_json(payload_range, payload_tree, pinfo)
        end
    end

    if actual_payload_length > declared_payload_length then
        packet_tree:add(
            fields.trailing,
            buffer(expected_packet_length, actual_payload_length - declared_payload_length)
        )
    end

    return captured_length
end

DissectorTable.get("udp.port"):add(DART_PORT, dart)
