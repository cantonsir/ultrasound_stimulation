"""
Brainsight network client and parallel recorder.

Provides:
- Low-level helpers (framing, matrix conversion, TSV formatting) shared with
  the standalone `scripts/brainsight_logger.py`.
- `BrainsightRecorder`: a thread-safe recorder that can be started and
  stopped alongside an ultrasound session. Logs mirror the Polaris
  Stream-to-File format plus a raw JSONL of every packet received.
"""
from __future__ import annotations

import json
import socket
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

RECORD_SEPARATOR = b"\x1e"  # Brainsight record separator (ASCII RS)

STREAMS = [
    "stream:session-crosshairs-moved",
    "stream:target-selected",
    "stream:sample-creation",
    "stream:sample-emg",
    "stream:session-polaris-update",
    "stream:session-ttl-triggers",
]


def format_value(value):
    if value is None:
        return "(null)"
    if isinstance(value, float):
        return f"{value:.9f}"
    return str(value)


def split_timestamp(ts: str):
    # Brainsight network timestamps are ISO 8601 UTC, e.g. 2026-04-06T06:10:33.430Z
    if not ts:
        return "(null)", "(null)"
    ts = ts.rstrip("Z")
    if "T" in ts:
        d, t = ts.split("T", 1)
        return d, t
    return ts, "(null)"


def matrix_to_stream_to_file_fields(matrix):
    """
    Convert Brainsight row-major 4x4 matrix:
      [r00,r01,r02,tx, r10,r11,r12,ty, r20,r21,r22,tz, 0,0,0,1]
    to Stream-to-File columns:
      x y z m0n0 m0n1 m0n2 m1n0 m1n1 m1n2 m2n0 m2n1 m2n2
    """
    if not matrix or len(matrix) != 16:
        return ["(null)"] * 12

    r00, r01, r02, tx = matrix[0], matrix[1], matrix[2], matrix[3]
    r10, r11, r12, ty = matrix[4], matrix[5], matrix[6], matrix[7]
    r20, r21, r22, tz = matrix[8], matrix[9], matrix[10], matrix[11]

    return [
        format_value(tx), format_value(ty), format_value(tz),
        format_value(r00), format_value(r10), format_value(r20),   # m0n0 m0n1 m0n2
        format_value(r01), format_value(r11), format_value(r21),   # m1n0 m1n1 m1n2
        format_value(r02), format_value(r12), format_value(r22),   # m2n0 m2n1 m2n2
    ]


class BrainsightClient:
    def __init__(self, host, port, timeout: Optional[float] = None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if timeout is not None:
            self.sock.settimeout(timeout)
        self.sock.connect((host, port))
        self.buffer = b""

    def send_packet(self, obj):
        payload = json.dumps(obj).encode("utf-8") + RECORD_SEPARATOR
        self.sock.sendall(payload)

    def recv_packets(self):
        chunk = self.sock.recv(4096)
        if not chunk:
            raise ConnectionError("Socket closed by server")

        self.buffer += chunk
        packets = []

        while RECORD_SEPARATOR in self.buffer:
            raw, self.buffer = self.buffer.split(RECORD_SEPARATOR, 1)
            if not raw.strip():
                continue
            packets.append(json.loads(raw.decode("utf-8")))

        return packets

    def settimeout(self, timeout: Optional[float]) -> None:
        self.sock.settimeout(timeout)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def enable_streams(client: BrainsightClient) -> None:
    for stream_name in STREAMS:
        client.send_packet({
            "packet-name": "request:set-stream-option",
            "packet-uuid": str(uuid.uuid4()).upper(),
            "stream-name": stream_name,
            "stream-value": True,
        })


def write_polaris_header(file_handle) -> None:
    """Write the Brainsight Stream-to-File format header to the TSV file."""
    file_handle.write("# Version: custom-from-network\n")
    file_handle.write("# Source: Brainsight network server\n")
    file_handle.write("# Units: millimetres, milliseconds, and microvolts where applicable\n")
    file_handle.write("# Encoding: UTF-8\n")
    file_handle.write("# Notes: Tab-delimited. This file mirrors the Polaris Tool rows of Stream to File.\n")
    file_handle.write(
        "# Polaris Tool\tDate\tTime\tPolaris Frame Number\tCalibration/Tracker Name\t"
        "Coordinate System\tx\ty\tz\tm0n0\tm0n1\tm0n2\tm1n0\tm1n1\tm1n2\tm2n0\tm2n1\tm2n2\n"
    )


def write_polaris_row(file_handle, packet, coord_name, matrix) -> None:
    """Format and write a single Polaris tracking row to the TSV file."""
    date_str, time_str = split_timestamp(packet.get("timestamp"))
    frame = packet.get("frame-number", "(null)")
    serial = packet.get("serial-number", "(null)")
    fields = matrix_to_stream_to_file_fields(matrix)

    row = [
        "Polaris Tool",
        date_str,
        time_str,
        str(frame),
        str(serial),
        coord_name if coord_name is not None else "(null)",
        *fields,
    ]
    file_handle.write("\t".join(row) + "\n")


class BrainsightRecorder:
    """
    Runs a Brainsight TCP listener in a background thread while the main
    session does something else (e.g. driving ultrasound bursts).

    Usage:
        recorder = BrainsightRecorder(host, port, log_dir, stamp, logger)
        if recorder.start():
            try:
                ... run session ...
            finally:
                recorder.stop()
    """

    CONNECT_TIMEOUT_S = 3.0
    RECV_TIMEOUT_S = 1.0

    def __init__(
        self,
        host: str,
        port: int,
        log_dir: Path,
        session_stamp: str,
        logger,
    ):
        self._host = host
        self._port = port
        self._log_dir = Path(log_dir)
        self._stamp = session_stamp
        self._logger = logger

        self._client: Optional[BrainsightClient] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._raw_fp = None
        self._polaris_fp = None

        self.raw_path = self._log_dir / f"brainsight_raw_{self._stamp}.jsonl"
        self.polaris_path = self._log_dir / f"brainsight_polaris_{self._stamp}.tsv"

    def start(self) -> bool:
        """Connect, enable streams, and spawn the listener thread.

        Returns True on success, False if the connection failed. On failure
        nothing is allocated and no log files are created.
        """
        try:
            self._client = BrainsightClient(self._host, self._port, timeout=self.CONNECT_TIMEOUT_S)
            enable_streams(self._client)
            # Switch to a short recv timeout so the thread can notice stop().
            self._client.settimeout(self.RECV_TIMEOUT_S)
        except (OSError, socket.timeout, ConnectionError) as exc:
            self._logger.log("brainsight_connect_failed", f"{self._host}:{self._port} {exc}")
            self._client = None
            return False

        self._log_dir.mkdir(parents=True, exist_ok=True)
        # Binary mode lets us write raw UTF-8 bytes without newline translation
        # while still flushing to disk promptly.
        self._raw_fp = self.raw_path.open("a", encoding="utf-8")
        self._polaris_fp = self.polaris_path.open("w", encoding="utf-8")
        write_polaris_header(self._polaris_fp)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="brainsight-recorder",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Signal the listener to stop, close the socket, and join the thread."""
        if self._thread is None:
            return
        self._stop_event.set()
        # Closing the socket unblocks any in-flight recv().
        if self._client is not None:
            try:
                self._client.close()
            except OSError:
                pass
        self._thread.join(timeout=self.RECV_TIMEOUT_S * 3)
        self._thread = None
        self._client = None
        if self._raw_fp is not None:
            self._raw_fp.close()
            self._raw_fp = None
        if self._polaris_fp is not None:
            self._polaris_fp.close()
            self._polaris_fp = None

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    packets = self._client.recv_packets()
                except socket.timeout:
                    continue
                except (OSError, ConnectionError):
                    if self._stop_event.is_set():
                        return
                    raise

                for packet in packets:
                    self._raw_fp.write(json.dumps(packet, ensure_ascii=False) + "\n")

                    if packet.get("packet-name") == "stream:session-polaris-update":
                        write_polaris_row(
                            self._polaris_fp,
                            packet,
                            "Polaris",
                            packet.get("tool-in-sensor"),
                        )
                        if "tool-in-desired" in packet:
                            coord = packet.get("coordinate-system", "(unknown)")
                            write_polaris_row(
                                self._polaris_fp,
                                packet,
                                coord,
                                packet.get("tool-in-desired"),
                            )

                self._raw_fp.flush()
                self._polaris_fp.flush()
        except Exception as exc:  # noqa: BLE001 — thread must not crash session
            self._logger.log("brainsight_error", str(exc))
