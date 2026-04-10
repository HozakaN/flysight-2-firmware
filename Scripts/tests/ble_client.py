"""
FlySight 2 BLE client for automated testing.

Provides the FlySightBLE class for connecting to a FlySight 2 device
over Bluetooth Low Energy, handling pairing, file transfer (Go-Back-N ARQ),
GNSS data streaming, and device state queries.
"""

import asyncio
from struct import unpack

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


# --- BLE UUIDs ---

# File Transfer service
FT_PACKET_OUT_UUID = "00000001-8e22-4541-9d4c-21edae82ed19"  # Notify (FS → Central)
FT_PACKET_IN_UUID = "00000002-8e22-4541-9d4c-21edae82ed19"   # Write (Central → FS)

# Sensor Data service
SD_GNSS_UUID = "00000000-8e22-4541-9d4c-21edae82ed19"        # Notify (GNSS data)
SD_CONTROL_POINT_UUID = "00000006-8e22-4541-9d4c-21edae82ed19"

# Starter Pistol service
SP_CONTROL_POINT_UUID = "00000003-8e22-4541-9d4c-21edae82ed19"
SP_RESULT_UUID = "00000004-8e22-4541-9d4c-21edae82ed19"

# Device State service
DS_MODE_UUID = "00000005-8e22-4541-9d4c-21edae82ed19"
DS_CONTROL_POINT_UUID = "00000007-8e22-4541-9d4c-21edae82ed19"

# Battery service (standard BLE)
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

# Manufacturer ID for FlySight (Bionic Avionics Inc.)
FLYSIGHT_MANUFACTURER_ID = 0x09DB

# File transfer constants
WINDOW_LENGTH = 8
FRAME_LENGTH = 242
TX_TIMEOUT = 1.0
RX_TIMEOUT = 1.0

# File transfer opcodes
OP_DELETE = 0x01
OP_READ = 0x02
OP_WRITE = 0x03
OP_MKDIR = 0x04
OP_LIST_DIR = 0x05
OP_DATA = 0x10
OP_FILE_INFO = 0x11
OP_ACK_DATA = 0x12
OP_NAK = 0xF0
OP_ACK = 0xF1
OP_PING = 0xFE
OP_CANCEL = 0xFF

# Sensor Data control point opcodes
SD_CMD_SET_GNSS_BLE_MASK = 0x01
SD_CMD_GET_GNSS_BLE_MASK = 0x02

# Control point response
CP_RESPONSE_ID = 0xF0
CP_STATUS_SUCCESS = 0x01


class ScanResult:
    """Result from scanning for a FlySight device."""

    def __init__(self, device: BLEDevice, pairing_mode: bool):
        self.device = device
        self.address = device.address
        self.name = device.name
        self.pairing_mode = pairing_mode


async def scan_for_flysight(timeout: float = 10.0) -> ScanResult | None:
    """Scan for a FlySight 2 device and return its address and pairing status."""
    result = None

    def detection_callback(device: BLEDevice, adv: AdvertisementData):
        nonlocal result
        if FLYSIGHT_MANUFACTURER_ID in adv.manufacturer_data:
            mfr_data = adv.manufacturer_data[FLYSIGHT_MANUFACTURER_ID]
            pairing_mode = len(mfr_data) > 0 and mfr_data[0] == 0x01
            result = ScanResult(device, pairing_mode)

    scanner = BleakScanner(detection_callback=detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return result


class FlySightBLE:
    """BLE client for FlySight 2 device."""

    def __init__(self):
        self._client: BleakClient | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self, address: str, timeout: float = 20.0):
        """Connect to a FlySight device by BLE address."""
        self._client = BleakClient(address, timeout=timeout)
        await self._client.connect()

    async def disconnect(self):
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def pair(self):
        """Initiate pairing/bonding with the device."""
        await self._client.pair(protection_level=2)

    # --- Ping ---

    async def ping(self, timeout: float = 5.0) -> bool:
        """Send a ping and wait for ACK. Returns True if ACK received."""
        ack_event = asyncio.Event()

        def on_notify(sender, data):
            if len(data) >= 2 and data[0] == OP_ACK and data[1] == OP_PING:
                ack_event.set()

        await self._client.start_notify(FT_PACKET_OUT_UUID, on_notify)
        await self._client.write_gatt_char(
            FT_PACKET_IN_UUID, bytes([OP_PING]), response=False
        )

        try:
            await asyncio.wait_for(ack_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            await self._client.stop_notify(FT_PACKET_OUT_UUID)

    # --- File Transfer ---

    async def write_file_from_bytes(self, data: bytes, remote_path: str):
        """Write bytes to a file on the device using Go-Back-N ARQ."""
        next_packet_num = 0
        next_ack_num = 0
        last_packet_num = -1
        ack_received = asyncio.Event()

        def on_notify(sender, rx_data):
            nonlocal next_ack_num
            if rx_data[0] == OP_ACK_DATA:
                ack_num = rx_data[1]
                if ack_num == (next_ack_num & 0xFF):
                    next_ack_num += 1
                    ack_received.set()

        await self._client.start_notify(FT_PACKET_OUT_UUID, on_notify)
        await self._client.write_gatt_char(
            FT_PACKET_IN_UUID,
            bytes([OP_WRITE]) + remote_path.encode(),
            response=True,
        )

        while next_ack_num != last_packet_num:
            while (
                next_packet_num < next_ack_num + WINDOW_LENGTH
                and next_packet_num != last_packet_num
            ):
                i = next_packet_num * FRAME_LENGTH
                pkt_num_byte = (next_packet_num & 0xFF).to_bytes(1, "little")
                if i < len(data):
                    chunk = data[i : i + FRAME_LENGTH]
                    await self._client.write_gatt_char(
                        FT_PACKET_IN_UUID,
                        bytes([OP_DATA]) + pkt_num_byte + chunk,
                        response=False,
                    )
                else:
                    await self._client.write_gatt_char(
                        FT_PACKET_IN_UUID,
                        bytes([OP_DATA]) + pkt_num_byte,
                        response=False,
                    )
                    last_packet_num = next_packet_num + 1

                next_packet_num += 1

            try:
                await asyncio.wait_for(ack_received.wait(), TX_TIMEOUT)
                ack_received.clear()
            except asyncio.TimeoutError:
                next_packet_num = next_ack_num

        await self._client.stop_notify(FT_PACKET_OUT_UUID)

    async def read_file(self, remote_path: str, timeout: float = RX_TIMEOUT) -> bytes:
        """Read a file from the device. Returns the file contents as bytes."""
        file_data = bytearray()
        transfer_complete = asyncio.Event()
        packet_received = asyncio.Event()
        next_packet_num = 0

        async def on_notify(sender, data):
            nonlocal next_packet_num
            if data[0] == OP_DATA:
                pkt_num = data[1]
                if pkt_num == (next_packet_num & 0xFF):
                    if len(data) > 2:
                        file_data.extend(data[2:])
                    else:
                        transfer_complete.set()
                    next_packet_num += 1
                    ack = bytes([OP_ACK_DATA, pkt_num])
                    await self._client.write_gatt_char(
                        FT_PACKET_IN_UUID, ack, response=False
                    )
                    packet_received.set()

        await self._client.start_notify(FT_PACKET_OUT_UUID, on_notify)
        offset_bytes = (0).to_bytes(4, "little")
        stride_bytes = (0).to_bytes(4, "little")
        await self._client.write_gatt_char(
            FT_PACKET_IN_UUID,
            bytes([OP_READ]) + offset_bytes + stride_bytes + remote_path.encode(),
            response=False,
        )

        try:
            while not transfer_complete.is_set():
                packet_received.clear()
                await asyncio.wait_for(packet_received.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            await self._client.stop_notify(FT_PACKET_OUT_UUID)

        return bytes(file_data)

    async def delete_file(self, path: str):
        """Delete a file or directory on the device."""
        await self._client.write_gatt_char(
            FT_PACKET_IN_UUID, bytes([OP_DELETE]) + path.encode(), response=False
        )

    async def mkdir(self, path: str):
        """Create a directory on the device."""
        await self._client.write_gatt_char(
            FT_PACKET_IN_UUID, bytes([OP_MKDIR]) + path.encode(), response=False
        )

    async def list_dir(self, path: str, timeout: float = 5.0) -> list[str]:
        """List directory contents. Returns list of parsed file info strings."""
        entries = []
        next_packet_num = 0
        done = asyncio.Event()

        def on_notify(sender, data):
            nonlocal next_packet_num
            if data[0] == OP_FILE_INFO:
                pkt_num = data[1]
                if pkt_num == (next_packet_num & 0xFF):
                    parsed = _parse_filinfo(data[2:])
                    if parsed is None:
                        done.set()
                    else:
                        entries.append(parsed)
                    next_packet_num += 1

        await self._client.start_notify(FT_PACKET_OUT_UUID, on_notify)
        await self._client.write_gatt_char(
            FT_PACKET_IN_UUID, bytes([OP_LIST_DIR]) + path.encode(), response=False
        )

        try:
            await asyncio.wait_for(done.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            await self._client.stop_notify(FT_PACKET_OUT_UUID)

        return entries

    # --- GNSS ---

    async def subscribe_gnss(self, callback):
        """Subscribe to GNSS measurement notifications."""
        await self._client.start_notify(SD_GNSS_UUID, lambda s, d: callback(d))

    async def unsubscribe_gnss(self):
        """Unsubscribe from GNSS notifications."""
        await self._client.stop_notify(SD_GNSS_UUID)

    async def set_gnss_mask(self, mask: int):
        """Set the GNSS BLE data mask via SD Control Point."""
        await self._client.write_gatt_char(
            SD_CONTROL_POINT_UUID,
            bytes([SD_CMD_SET_GNSS_BLE_MASK, mask]),
        )

    async def get_gnss_mask(self, timeout: float = 5.0) -> int:
        """Get the current GNSS BLE data mask."""
        result = asyncio.Event()
        mask_value = [0]

        def on_indicate(sender, data):
            if (
                len(data) >= 4
                and data[0] == CP_RESPONSE_ID
                and data[1] == SD_CMD_GET_GNSS_BLE_MASK
                and data[2] == CP_STATUS_SUCCESS
            ):
                mask_value[0] = data[3]
                result.set()

        await self._client.start_notify(SD_CONTROL_POINT_UUID, on_indicate)
        await self._client.write_gatt_char(
            SD_CONTROL_POINT_UUID, bytes([SD_CMD_GET_GNSS_BLE_MASK])
        )

        try:
            await asyncio.wait_for(result.wait(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("No response to GET_GNSS_BLE_MASK")
        finally:
            await self._client.stop_notify(SD_CONTROL_POINT_UUID)

        return mask_value[0]

    # --- Device State ---

    async def read_device_mode(self) -> int:
        """Read the current device mode (0=SLEEP, 1=ACTIVE, etc.)."""
        data = await self._client.read_gatt_char(DS_MODE_UUID)
        return data[0]

    async def subscribe_mode(self, callback):
        """Subscribe to device mode change indications."""
        await self._client.start_notify(
            DS_MODE_UUID, lambda s, d: callback(d[0])
        )

    async def unsubscribe_mode(self):
        """Unsubscribe from mode indications."""
        await self._client.stop_notify(DS_MODE_UUID)

    # --- Battery ---

    async def read_battery(self) -> int:
        """Read battery level (0-100%)."""
        data = await self._client.read_gatt_char(BATTERY_LEVEL_UUID)
        return data[0]

    async def subscribe_battery(self, callback):
        """Subscribe to battery level notifications."""
        await self._client.start_notify(
            BATTERY_LEVEL_UUID, lambda s, d: callback(d[0])
        )

    async def unsubscribe_battery(self):
        """Unsubscribe from battery notifications."""
        await self._client.stop_notify(BATTERY_LEVEL_UUID)


def _parse_filinfo(data: bytes) -> str | None:
    """Parse a FILINFO packet into a human-readable string."""
    fsize, fdate, ftime, fattrib, fname = unpack("<IHHB13s", data)
    fname = fname.split(b"\0", 1)[0].decode("ascii")
    if not fname:
        return None

    year = ((fdate >> 9) & 0x7F) + 1980
    month = (fdate >> 5) & 0x0F
    day = fdate & 0x1F
    hour = (ftime >> 11) & 0x1F
    minute = (ftime >> 5) & 0x3F
    second = (ftime & 0x1F) * 2

    attr_chars = []
    for bit, ch in [(0, "r"), (1, "h"), (2, "s"), (3, "a"), (4, "d")]:
        attr_chars.append(ch if fattrib & (1 << bit) else "-")

    return (
        f"{fsize} {year}-{month:02d}-{day:02d} "
        f"{hour:02d}:{minute:02d}:{second:02d} "
        f"{''.join(attr_chars)} {fname}"
    )
