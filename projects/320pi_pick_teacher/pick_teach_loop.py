#!/usr/bin/env python3
"""
myCobot320Pi fixed-point pick-and-place tool with host handshake.

The task is waypoint-based. Each item has its own pick waypoints, while the
wait pose and place side are currently shared across all items.
"""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import serial.tools.list_ports

# Force local repo imports before system site-packages.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pymycobot.mycobot320 import MyCobot320


TASK_VERSION = 3
DEFAULT_TASK_FILE = "pick_task_320pi.json"
DEFAULT_MOVE_SPEED = 70
DEFAULT_LINEAR_SPEED = 40
DEFAULT_SETTLE_TIME = 0.3
DEFAULT_GRIPPER_DELAY = 0.3
DEFAULT_GRIPPER_OPEN = 100
DEFAULT_GRIPPER_CLOSE = 0
DEFAULT_HANDSHAKE_TIMEOUT = 30.0
DEFAULT_SEND_MESSAGE = "OK"
DEFAULT_RECV_MESSAGE = "OK"
DEFAULT_ITEM_NAME = "item_1"

ITEM_POINT_NAMES = ["pick_approach", "pick_pose", "pick_lift"]
SHARED_POINT_NAMES = ["wait_pose", "place_approach", "place_pose", "place_retreat"]

POINT_LABELS = {
    "pick_approach": "Pick Approach",
    "pick_pose": "Pick Pose",
    "pick_lift": "Pick Lift",
    "wait_pose": "Wait Pose",
    "place_approach": "Place Approach",
    "place_pose": "Place Pose",
    "place_retreat": "Place Retreat",
}


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def select_serial_port() -> str:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        raise RuntimeError("No serial port found.")

    print("\nAvailable serial ports:")
    for idx, port in enumerate(ports, start=1):
        print(f"  {idx}. {port}")

    while True:
        raw = input(f"\nSelect port [1-{len(ports)}]: ").strip()
        try:
            selected = int(raw)
            if 1 <= selected <= len(ports):
                break
        except ValueError:
            pass
        print("Invalid input.")

    return str(ports[selected - 1]).split(" - ")[0].strip()


class GripperAdapter:
    def __init__(self, mc: MyCobot320, speed: int = 70, open_value: int = 100, close_value: int = 0):
        self.mc = mc
        self.speed = clamp(speed, 1, 100)
        self.open_value = clamp(open_value, 0, 100)
        self.close_value = clamp(close_value, 0, 100)

    def init(self) -> None:
        for name, args in [
            ("set_gripper_mode", (0,)),
            ("init_electric_gripper", ()),
            ("set_electric_gripper", (1,)),
        ]:
            fn = getattr(self.mc, name, None)
            if callable(fn):
                try:
                    fn(*args)
                    time.sleep(0.1)
                except Exception:
                    pass

    def open(self) -> None:
        if self._call_state(0):
            return
        if self._call_value(self.open_value):
            return
        raise RuntimeError("No supported gripper open API found on this pymycobot build.")

    def close(self) -> None:
        if self._call_state(1):
            return
        if self._call_value(self.close_value):
            return
        raise RuntimeError("No supported gripper close API found on this pymycobot build.")

    def _call_state(self, flag: int) -> bool:
        fn = getattr(self.mc, "set_gripper_state", None)
        if not callable(fn):
            return False
        try:
            fn(flag, self.speed)
            return True
        except TypeError:
            try:
                fn(flag, self.speed, 1)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def _call_value(self, value: int) -> bool:
        fn = getattr(self.mc, "set_gripper_value", None)
        if not callable(fn):
            return False
        try:
            fn(value, self.speed)
            return True
        except TypeError:
            try:
                fn(value, self.speed, 1)
                return True
            except Exception:
                return False
        except Exception:
            return False


class HostHandshakeClient:
    def __init__(self, host: str, port: int, timeout: float, enabled: bool = True):
        self.host = host.strip()
        self.port = int(port)
        self.timeout = max(0.1, timeout)
        self.enabled = enabled and bool(self.host) and self.port > 0
        self.sock: Optional[socket.socket] = None

    def connect(self) -> None:
        if not self.enabled:
            return
        self.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.host, self.port))
        except socket.timeout as exc:
            sock.close()
            raise RuntimeError(f"Timed out while connecting to host {self.host}:{self.port}.") from exc
        except OSError as exc:
            sock.close()
            raise RuntimeError(f"Failed to connect to host {self.host}:{self.port}: {exc}") from exc
        self.sock = sock

    def ensure_connected(self) -> None:
        if not self.enabled:
            return
        if self.sock is None:
            self.connect()

    def reconnect(self) -> None:
        if not self.enabled:
            return
        self.close()
        self.connect()

    def send_line(self, message: str) -> None:
        if not self.enabled:
            return
        self.ensure_connected()
        assert self.sock is not None
        data = (message.rstrip("\r\n") + "\n").encode("utf-8")
        try:
            self.sock.sendall(data)
        except socket.timeout as exc:
            raise RuntimeError("Timed out while sending handshake message to host.") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to send handshake message to host: {exc}") from exc

    def recv_line(self) -> str:
        if not self.enabled:
            return ""
        self.ensure_connected()
        assert self.sock is not None
        chunks = bytearray()
        while True:
            try:
                data = self.sock.recv(1)
            except socket.timeout as exc:
                raise RuntimeError("Timed out while waiting for host reply.") from exc
            except OSError as exc:
                raise RuntimeError(f"Failed while receiving host reply: {exc}") from exc
            if not data:
                raise RuntimeError("Host closed the socket.")
            if data == b"\n":
                return chunks.decode("utf-8", errors="replace").rstrip("\r")
            chunks.extend(data)

    def request_ack(self, send_message: str, expected_reply: str) -> str:
        if not self.enabled:
            return expected_reply
        self.send_line(send_message)
        reply = self.recv_line()
        if reply != expected_reply:
            raise RuntimeError(f"Unexpected host reply: {reply!r}, expected {expected_reply!r}")
        return reply

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


class TeachingProject:
    def __init__(
        self,
        mc: MyCobot320,
        task_path: str,
        move_speed: int,
        linear_speed: int,
        settle_time: float,
        gripper_delay: float,
        host: str,
        host_port: int,
        handshake_timeout: float,
        send_message: str,
        recv_message: str,
        network_enabled: bool,
    ):
        self.mc = mc
        self.task_path = task_path
        self.move_speed = clamp(move_speed, 1, 100)
        self.linear_speed = clamp(linear_speed, 1, 100)
        self.settle_time = max(0.0, settle_time)
        self.gripper_delay = max(0.0, gripper_delay)
        self.handshake_timeout = max(0.1, handshake_timeout)
        self.send_message = send_message
        self.recv_message = recv_message
        self.active_item = DEFAULT_ITEM_NAME
        self.gripper = GripperAdapter(mc)
        self.handshake = HostHandshakeClient(host, host_port, self.handshake_timeout, enabled=network_enabled)

        self.task: Dict[str, Any] = self._new_task()
        self._stop_loop = False

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{stamp}] {message}")

    def _new_task(self) -> Dict[str, Any]:
        return {
            "task_version": TASK_VERSION,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "network": {
                "mode": "client",
                "enabled": self.handshake.enabled,
                "host": self.handshake.host,
                "port": self.handshake.port,
            },
            "params": {
                "move_speed": self.move_speed,
                "linear_speed": self.linear_speed,
                "settle_time": self.settle_time,
                "gripper_delay": self.gripper_delay,
                "gripper_open_value": self.gripper.open_value,
                "gripper_close_value": self.gripper.close_value,
                "handshake_timeout": self.handshake_timeout,
                "send_message": self.send_message,
                "recv_message": self.recv_message,
            },
            "shared_points": {},
            "items": {
                DEFAULT_ITEM_NAME: {}
            },
            "sequence": [DEFAULT_ITEM_NAME],
        }

    def _ensure_item(self, item_name: str) -> None:
        items = self.task.setdefault("items", {})
        if item_name not in items:
            items[item_name] = {}
        sequence = self.task.setdefault("sequence", [])
        if item_name not in sequence:
            sequence.append(item_name)

    def _read_current_waypoint(self) -> Dict[str, Any]:
        angles = self.mc.get_angles()
        coords = self.mc.get_coords()
        encoders = self.mc.get_encoders()

        if not isinstance(angles, list) or len(angles) != 6:
            raise RuntimeError(f"Failed to read angles: {angles}")
        if not isinstance(coords, list) or len(coords) != 6:
            raise RuntimeError(f"Failed to read coords: {coords}")
        if not isinstance(encoders, list) or len(encoders) != 6:
            raise RuntimeError(f"Failed to read encoders: {encoders}")

        return {
            "angles": angles,
            "coords": coords,
            "encoders": encoders,
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

    def capture_item_point(self, point_name: str) -> None:
        self._ensure_item(self.active_item)
        point = self._read_current_waypoint()
        self.task["items"][self.active_item][point_name] = point
        print(f"Captured {self.active_item}.{point_name}:")
        print(f"  angles : {point['angles']}")
        print(f"  coords : {point['coords']}")

    def capture_shared_point(self, point_name: str) -> None:
        point = self._read_current_waypoint()
        self.task["shared_points"][point_name] = point
        print(f"Captured shared.{point_name}:")
        print(f"  angles : {point['angles']}")
        print(f"  coords : {point['coords']}")

    def show_pose(self) -> None:
        try:
            angles = self.mc.get_angles()
        except Exception:
            angles = None
        try:
            coords = self.mc.get_coords()
        except Exception:
            coords = None
        try:
            encoders = self.mc.get_encoders()
        except Exception:
            encoders = None
        print(f"angles  : {angles}")
        print(f"coords  : {coords}")
        print(f"encoders: {encoders}")

    def show_task_summary(self) -> None:
        print(f"Active item: {self.active_item}")
        print("Shared points:")
        shared = self.task.get("shared_points", {})
        for name in SHARED_POINT_NAMES:
            print(f"  {name:<15} {'SET' if name in shared else 'MISSING'}")

        print("Items:")
        items = self.task.get("items", {})
        for item_name in self.task.get("sequence", []):
            item_points = items.get(item_name, {})
            state = ", ".join(f"{name}={'Y' if name in item_points else 'N'}" for name in ITEM_POINT_NAMES)
            active_mark = " *" if item_name == self.active_item else ""
            print(f"  {item_name}{active_mark}: {state}")

    def add_item(self, item_name: str) -> None:
        item_name = item_name.strip()
        if not item_name:
            raise ValueError("Item name cannot be empty.")
        self._ensure_item(item_name)
        self.active_item = item_name
        print(f"Item ready: {item_name}")

    def set_active_item(self, item_name: str) -> None:
        items = self.task.get("items", {})
        if item_name not in items:
            raise ValueError(f"Unknown item: {item_name}")
        self.active_item = item_name
        print(f"Active item set to: {item_name}")

    def clear_task(self) -> None:
        self.task = self._new_task()
        self.active_item = DEFAULT_ITEM_NAME
        print("Task cleared.")

    def save(self) -> None:
        self.task["network"] = {
            "mode": "client",
            "enabled": self.handshake.enabled,
            "host": self.handshake.host,
            "port": self.handshake.port,
        }
        self.task["params"] = {
            "move_speed": self.move_speed,
            "linear_speed": self.linear_speed,
            "settle_time": self.settle_time,
            "gripper_delay": self.gripper_delay,
            "gripper_open_value": self.gripper.open_value,
            "gripper_close_value": self.gripper.close_value,
            "handshake_timeout": self.handshake_timeout,
            "send_message": self.send_message,
            "recv_message": self.recv_message,
        }
        os.makedirs(os.path.dirname(self.task_path) or ".", exist_ok=True)
        with open(self.task_path, "w", encoding="utf-8") as f:
            json.dump(self.task, f, ensure_ascii=False, indent=2)
        print(f"Task saved: {self.task_path}")

    def load(self) -> None:
        with open(self.task_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid task format.")
        if data.get("task_version") != TASK_VERSION:
            raise ValueError(f"Unsupported task version: {data.get('task_version')}")
        if not isinstance(data.get("shared_points"), dict):
            raise ValueError("Invalid task: missing shared_points.")
        if not isinstance(data.get("items"), dict):
            raise ValueError("Invalid task: missing items.")
        if not isinstance(data.get("sequence"), list):
            raise ValueError("Invalid task: missing sequence.")

        self.task = data

        network = data.get("network", {})
        if isinstance(network, dict):
            self.handshake.host = str(network.get("host", self.handshake.host))
            self.handshake.port = int(network.get("port", self.handshake.port))
            self.handshake.enabled = bool(network.get("enabled", self.handshake.enabled)) and bool(self.handshake.host) and self.handshake.port > 0
            self.handshake.close()

        params = data.get("params", {})
        if isinstance(params, dict):
            self.move_speed = clamp(int(params.get("move_speed", self.move_speed)), 1, 100)
            self.linear_speed = clamp(int(params.get("linear_speed", self.linear_speed)), 1, 100)
            self.settle_time = max(0.0, float(params.get("settle_time", self.settle_time)))
            self.gripper_delay = max(0.0, float(params.get("gripper_delay", self.gripper_delay)))
            self.handshake_timeout = max(0.1, float(params.get("handshake_timeout", self.handshake_timeout)))
            self.send_message = str(params.get("send_message", self.send_message))
            self.recv_message = str(params.get("recv_message", self.recv_message))
            self.gripper.open_value = clamp(int(params.get("gripper_open_value", self.gripper.open_value)), 0, 100)
            self.gripper.close_value = clamp(int(params.get("gripper_close_value", self.gripper.close_value)), 0, 100)
            self.handshake.timeout = self.handshake_timeout

        sequence = self.task.get("sequence", [])
        self.active_item = sequence[0] if sequence else DEFAULT_ITEM_NAME
        print(f"Task loaded: {self.task_path} | items={len(self.task['items'])} | sequence={self.task['sequence']}")

    def move_to_zero(self) -> None:
        self.mc.send_angles([0, 0, 0, 0, 0, 0], self.move_speed)
        self._log("Sent move_to_zero command.")

    def release(self) -> None:
        self.mc.release_all_servos()
        self._log("All servos released.")

    def power_on(self) -> None:
        self.mc.power_on()
        self._log("All servos powered on.")

    def _require_shared_points(self) -> None:
        shared = self.task.get("shared_points", {})
        missing = [name for name in SHARED_POINT_NAMES if name not in shared]
        if missing:
            raise RuntimeError(f"Missing shared points: {', '.join(missing)}")

    def _require_item_points(self, item_name: str) -> None:
        items = self.task.get("items", {})
        item = items.get(item_name)
        if not isinstance(item, dict):
            raise RuntimeError(f"Missing item: {item_name}")
        missing = [name for name in ITEM_POINT_NAMES if name not in item]
        if missing:
            raise RuntimeError(f"Missing points for {item_name}: {', '.join(missing)}")

    def _move_joint(self, point: Dict[str, Any], label: str, speed: Optional[int] = None) -> None:
        angles = point.get("angles")
        if not isinstance(angles, list) or len(angles) != 6:
            raise RuntimeError(f"{label} missing valid angles.")
        used_speed = clamp(int(speed if speed is not None else self.move_speed), 1, 100)
        self._log(f"MoveJ -> {label} @ speed={used_speed}")
        self.mc.sync_send_angles(angles, used_speed)
        if self.settle_time > 0:
            time.sleep(self.settle_time)

    def _move_linear(self, point: Dict[str, Any], label: str, speed: Optional[int] = None) -> None:
        coords = point.get("coords")
        if not isinstance(coords, list) or len(coords) != 6:
            raise RuntimeError(f"{label} missing valid coords.")
        used_speed = clamp(int(speed if speed is not None else self.linear_speed), 1, 100)
        self._log(f"MoveL -> {label} @ speed={used_speed}")
        self.mc.sync_send_coords(coords, used_speed, mode=1)
        if self.settle_time > 0:
            time.sleep(self.settle_time)

    def _gripper_open(self) -> None:
        self._log("Gripper OPEN")
        self.gripper.open()
        if self.gripper_delay > 0:
            time.sleep(self.gripper_delay)

    def _gripper_close(self) -> None:
        self._log("Gripper CLOSE")
        self.gripper.close()
        if self.gripper_delay > 0:
            time.sleep(self.gripper_delay)

    def _host_handshake(self) -> None:
        if not self.handshake.enabled:
            self._log("Handshake disabled: skipping host wait.")
            return
        message = self.send_message
        expected = self.recv_message
        self._log(
            f"Handshake start: host={self.handshake.host}:{self.handshake.port}, timeout={self.handshake.timeout}s"
        )
        self._log("Handshake reconnecting to host...")
        self.handshake.reconnect()
        self._log(f"Handshake send: {message!r}")
        self.handshake.send_line(message)
        self._log("Handshake waiting reply...")
        reply = self.handshake.recv_line()
        self._log(f"Handshake reply: {reply!r}")
        if reply != expected:
            raise RuntimeError(f"Unexpected host reply: {reply!r}, expected {expected!r}")
        self._log("Handshake OK.")

    def _run_item(self, item_name: str) -> None:
        self._require_shared_points()
        self._require_item_points(item_name)

        shared = self.task["shared_points"]
        item = self.task["items"][item_name]

        if self._stop_loop:
            return
        self._gripper_open()

        if self._stop_loop:
            return
        self._move_joint(item["pick_approach"], f"{item_name}.pick_approach")

        if self._stop_loop:
            return
        self._move_linear(item["pick_pose"], f"{item_name}.pick_pose")

        if self._stop_loop:
            return
        self._gripper_close()

        if self._stop_loop:
            return
        self._move_linear(item["pick_lift"], f"{item_name}.pick_lift")

        if self._stop_loop:
            return
        self._move_joint(shared["wait_pose"], "shared.wait_pose")

        if self._stop_loop:
            return
        self._host_handshake()

        if self._stop_loop:
            return
        self._move_joint(shared["place_approach"], "shared.place_approach")

        if self._stop_loop:
            return
        self._move_linear(shared["place_pose"], "shared.place_pose")

        if self._stop_loop:
            return
        self._gripper_open()

        if self._stop_loop:
            return
        self._move_linear(shared["place_retreat"], "shared.place_retreat")

    def run_once(self) -> None:
        sequence = self.task.get("sequence", [])
        if not sequence:
            raise RuntimeError("Task sequence is empty.")
        self._stop_loop = False
        for item_name in sequence:
            if self._stop_loop:
                break
            print(f"\n=== item {item_name} ===")
            self._run_item(item_name)
        print("Run-once finished.")

    def stop_loop(self) -> None:
        self._stop_loop = True
        print("Loop stop requested.")

    def run_loop(self) -> None:
        sequence = self.task.get("sequence", [])
        if not sequence:
            raise RuntimeError("Task sequence is empty.")
        self._stop_loop = False
        print("Loop started. Press 'e' in menu to stop after current step.")
        cycle = 0
        while not self._stop_loop:
            cycle += 1
            print(f"\n--- cycle {cycle} ---")
            for item_name in sequence:
                if self._stop_loop:
                    break
                print(f"\n=== item {item_name} ===")
                self._run_item(item_name)
        print("Loop stopped.")

    def print_menu(self) -> None:
        print(
            """
Commands
  h                     -> show this menu
  p                     -> print current joint angles/coords/encoders
  z                     -> move to zero angles [0,0,0,0,0,0]
  f                     -> release all servos (free drag)
  m                     -> power on all servos
  item <name>           -> create/select active item
  seq                   -> show current sequence
  v                     -> show task summary
  1                     -> capture active_item.pick_approach
  2                     -> capture active_item.pick_pose
  3                     -> capture active_item.pick_lift
  4                     -> capture shared.wait_pose
  5                     -> capture shared.place_approach
  6                     -> capture shared.place_pose
  7                     -> capture shared.place_retreat
  o                     -> gripper OPEN
  k                     -> gripper CLOSE
  net                   -> show network config
  ping                  -> connect to host now
  s                     -> save task JSON
  l                     -> load task JSON
  x                     -> clear entire task
  t                     -> dry-run once
  y                     -> run loop
  e                     -> stop current loop
  q                     -> quit
"""
        )

    def show_network(self) -> None:
        print("Network:")
        print(f"  enabled : {self.handshake.enabled}")
        print(f"  host    : {self.handshake.host}")
        print(f"  port    : {self.handshake.port}")
        print(f"  timeout : {self.handshake.timeout}")
        print(f"  send    : {self.send_message}")
        print(f"  expect  : {self.recv_message}")

    def run_cli(self) -> None:
        self.gripper.init()
        self.print_menu()

        while True:
            raw = input("\ncmd> ").strip()
            cmd = raw.lower()
            try:
                if cmd == "h":
                    self.print_menu()
                elif cmd == "p":
                    self.show_pose()
                elif cmd == "z":
                    self.move_to_zero()
                elif cmd == "f":
                    self.release()
                elif cmd == "m":
                    self.power_on()
                elif cmd == "1":
                    self.capture_item_point("pick_approach")
                elif cmd == "2":
                    self.capture_item_point("pick_pose")
                elif cmd == "3":
                    self.capture_item_point("pick_lift")
                elif cmd == "4":
                    self.capture_shared_point("wait_pose")
                elif cmd == "5":
                    self.capture_shared_point("place_approach")
                elif cmd == "6":
                    self.capture_shared_point("place_pose")
                elif cmd == "7":
                    self.capture_shared_point("place_retreat")
                elif cmd == "v":
                    self.show_task_summary()
                elif cmd == "o":
                    self._gripper_open()
                elif cmd == "k":
                    self._gripper_close()
                elif cmd == "net":
                    self.show_network()
                elif cmd == "ping":
                    self.handshake.connect()
                    print("Host connected.")
                elif cmd == "s":
                    self.save()
                elif cmd == "l":
                    self.load()
                elif cmd == "x":
                    self.clear_task()
                elif cmd == "t":
                    self.run_once()
                elif cmd == "y":
                    self.run_loop()
                elif cmd == "e":
                    self.stop_loop()
                elif cmd == "q":
                    self.stop_loop()
                    break
                elif raw.startswith("item "):
                    self.add_item(raw.split(" ", 1)[1].strip())
                elif cmd == "seq":
                    print(self.task.get("sequence", []))
                else:
                    print("Unknown command. Press 'h' for help.")
            except Exception as e:
                print(f"Command failed: {e}")

        self.handshake.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="myCobot320Pi pick-and-place tool with host handshake")
    parser.add_argument("--port", type=str, default="", help="Serial port, e.g. /dev/ttyAMA0")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--task", type=str, default=DEFAULT_TASK_FILE, help="Task JSON path")
    parser.add_argument("--speed", type=int, default=DEFAULT_MOVE_SPEED, help="Joint move speed [1..100]")
    parser.add_argument("--linear-speed", type=int, default=DEFAULT_LINEAR_SPEED, help="Linear move speed [1..100]")
    parser.add_argument("--settle-time", type=float, default=DEFAULT_SETTLE_TIME, help="Pause after each move")
    parser.add_argument("--gripper-delay", type=float, default=DEFAULT_GRIPPER_DELAY, help="Pause after gripper actions")
    parser.add_argument("--host", type=str, default="", help="Host IP for TCP handshake")
    parser.add_argument("--host-port", type=int, default=0, help="Host TCP port for handshake")
    parser.add_argument("--handshake-timeout", type=float, default=DEFAULT_HANDSHAKE_TIMEOUT, help="Socket timeout seconds")
    parser.add_argument("--send-message", type=str, default=DEFAULT_SEND_MESSAGE, help="Exact message sent to host")
    parser.add_argument("--recv-message", type=str, default=DEFAULT_RECV_MESSAGE, help="Exact message expected from host")
    parser.add_argument("--no-network", action="store_true", help="Disable TCP handshake")
    parser.add_argument("--debug", action="store_true", help="Enable pymycobot debug")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        port = args.port.strip() if args.port else select_serial_port()
        print(f"\nConnecting: port={port}, baud={args.baud}")
        mc = MyCobot320(port, args.baud, debug=args.debug)
        time.sleep(0.5)

        project = TeachingProject(
            mc=mc,
            task_path=args.task,
            move_speed=args.speed,
            linear_speed=args.linear_speed,
            settle_time=args.settle_time,
            gripper_delay=args.gripper_delay,
            host=args.host,
            host_port=args.host_port,
            handshake_timeout=args.handshake_timeout,
            send_message=args.send_message,
            recv_message=args.recv_message,
            network_enabled=not args.no_network,
        )
        project.run_cli()
        print("Bye.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        print(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
