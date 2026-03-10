#!/usr/bin/env python3
"""
myCobot320Pi pick-and-place teaching runner.

Features:
- hand-guided trajectory recording
- gripper open/close/wait events
- fixed start/end pose for each cycle
- loop execution with emergency stop
- save/load task file (JSON)
"""

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import serial.tools.list_ports

from pymycobot.mycobot320 import MyCobot320


TASK_VERSION = 1
DEFAULT_TASK_FILE = "pick_task_320pi.json"


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


class TeachingProject:
    def __init__(self, mc: MyCobot320, task_path: str, move_speed: int, sample_dt: float):
        self.mc = mc
        self.task_path = task_path
        self.move_speed = clamp(move_speed, 1, 100)
        self.sample_dt = max(0.02, sample_dt)

        self.gripper = GripperAdapter(mc)
        self.task: Dict[str, Any] = self._new_task()

        self._recording = False
        self._record_thread: Optional[threading.Thread] = None
        self._stop_loop = False

    def _new_task(self) -> Dict[str, Any]:
        return {
            "task_version": TASK_VERSION,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "start_encoders": None,
            "end_encoders": None,
            "items": [],
            "meta": {
                "move_speed": self.move_speed,
                "sample_dt": self.sample_dt,
            },
        }

    def print_menu(self) -> None:
        print(
            """
Commands
  h  -> show this menu
  p  -> print current joint angles/encoders
  z  -> move to zero angles [0,0,0,0,0,0]
  f  -> release all servos (free drag)
  m  -> power on all servos
  1  -> capture current pose as FIXED START
  2  -> capture current pose as FIXED END
  r  -> start recording trajectory by hand guiding
  c  -> stop recording
  o  -> insert gripper OPEN event
  k  -> insert gripper CLOSE event
  w  -> insert wait event (seconds)
  x  -> clear all recorded items
  s  -> save task JSON
  l  -> load task JSON
  t  -> dry-run once (no loop)
  y  -> run loop
  e  -> stop current loop
  q  -> quit
"""
        )

    def show_pose(self) -> None:
        try:
            angles = self.mc.get_angles()
        except Exception:
            angles = None
        try:
            encoders = self.mc.get_encoders()
        except Exception:
            encoders = None
        print(f"angles  : {angles}")
        print(f"encoders: {encoders}")

    def capture_start(self) -> None:
        encoders = self.mc.get_encoders()
        if not encoders:
            print("Failed: cannot read encoders.")
            return
        self.task["start_encoders"] = encoders
        print(f"Captured fixed START: {encoders}")

    def capture_end(self) -> None:
        encoders = self.mc.get_encoders()
        if not encoders:
            print("Failed: cannot read encoders.")
            return
        self.task["end_encoders"] = encoders
        print(f"Captured fixed END: {encoders}")

    def clear_items(self) -> None:
        self.task["items"] = []
        print("All recorded items cleared.")

    def start_record(self) -> None:
        if self._recording:
            print("Already recording.")
            return
        self._recording = True
        print("Recording started.")

        def worker() -> None:
            while self._recording:
                encoders = self.mc.get_encoders()
                speeds = self.mc.get_servo_speeds()
                if encoders:
                    item = {
                        "type": "motion",
                        "encoders": encoders,
                        "speeds": speeds if isinstance(speeds, list) else [],
                        "dt": self.sample_dt,
                    }
                    self.task["items"].append(item)
                time.sleep(self.sample_dt)

        self._record_thread = threading.Thread(target=worker, daemon=True)
        self._record_thread.start()

    def stop_record(self) -> None:
        if not self._recording:
            print("Not recording.")
            return
        self._recording = False
        if self._record_thread:
            self._record_thread.join()
            self._record_thread = None
        print("Recording stopped.")

    def add_wait_event(self) -> None:
        raw = input("Wait seconds: ").strip()
        try:
            sec = float(raw)
            if sec < 0:
                raise ValueError
        except ValueError:
            print("Invalid seconds.")
            return
        self.task["items"].append({"type": "wait", "seconds": sec})
        print(f"Added wait event: {sec}s")

    def add_gripper_open(self) -> None:
        self.task["items"].append({"type": "gripper_open", "seconds_after": 0.3})
        print("Added gripper OPEN event.")

    def add_gripper_close(self) -> None:
        self.task["items"].append({"type": "gripper_close", "seconds_after": 0.3})
        print("Added gripper CLOSE event.")

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.task_path) or ".", exist_ok=True)
        with open(self.task_path, "w", encoding="utf-8") as f:
            json.dump(self.task, f, ensure_ascii=False, indent=2)
        print(f"Task saved: {self.task_path}")

    def load(self) -> None:
        with open(self.task_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid task format.")
        if "items" not in data or not isinstance(data["items"], list):
            raise ValueError("Invalid task: missing items list.")
        self.task = data
        print(
            "Task loaded: "
            f"{self.task_path} | items={len(self.task['items'])} | "
            f"start_set={self.task.get('start_encoders') is not None} | "
            f"end_set={self.task.get('end_encoders') is not None}"
        )

    def move_to_zero(self) -> None:
        self.mc.send_angles([0, 0, 0, 0, 0, 0], self.move_speed)
        print("Sent move_to_zero command.")

    def release(self) -> None:
        self.mc.release_all_servos()
        print("All servos released.")

    def power_on(self) -> None:
        self.mc.power_on()
        print("All servos powered on.")

    def run_once(self) -> None:
        self._stop_loop = False
        self._execute_cycle()
        print("Run-once finished.")

    def stop_loop(self) -> None:
        self._stop_loop = True
        print("Loop stop requested.")

    def run_loop(self) -> None:
        if not self.task.get("items"):
            print("No items to run.")
            return
        self._stop_loop = False
        print("Loop started. Press 'e' in menu to stop after current step.")
        cycle = 0
        while not self._stop_loop:
            cycle += 1
            print(f"\n--- cycle {cycle} ---")
            self._execute_cycle()
        print("Loop stopped.")

    def _move_to_encoders(self, encoders: List[int]) -> None:
        if not encoders:
            return

        send_drag = getattr(self.mc, "send_encoders_drag", None)
        if callable(send_drag):
            send_drag(encoders, [self.move_speed] * 6)
            return

        set_drag = getattr(self.mc, "set_encoders_drag", None)
        if callable(set_drag):
            set_drag(encoders, [self.move_speed] * 6)
            return

        self.mc.set_encoders(encoders, self.move_speed)

    def _exec_item(self, item: Dict[str, Any]) -> None:
        t = item.get("type")
        if t == "motion":
            encoders = item.get("encoders")
            speeds = item.get("speeds", [])
            dt = float(item.get("dt", self.sample_dt))
            if not isinstance(encoders, list):
                return

            send_drag = getattr(self.mc, "send_encoders_drag", None)
            if callable(send_drag):
                if isinstance(speeds, list) and len(speeds) == 6:
                    speeds_use = [clamp(int(abs(v)), 1, 100) for v in speeds]
                else:
                    speeds_use = [self.move_speed] * 6
                send_drag(encoders, speeds_use)
            else:
                self.mc.set_encoders(encoders, self.move_speed)
            time.sleep(max(0.01, dt))
            return

        if t == "wait":
            sec = float(item.get("seconds", 0))
            time.sleep(max(0.0, sec))
            return

        if t == "gripper_open":
            self.gripper.open()
            time.sleep(float(item.get("seconds_after", 0.3)))
            return

        if t == "gripper_close":
            self.gripper.close()
            time.sleep(float(item.get("seconds_after", 0.3)))
            return

    def _execute_cycle(self) -> None:
        start_encoders = self.task.get("start_encoders")
        end_encoders = self.task.get("end_encoders")
        items = self.task.get("items", [])

        if start_encoders:
            self._move_to_encoders(start_encoders)
            time.sleep(0.6)

        for item in items:
            if self._stop_loop:
                return
            self._exec_item(item)

        if end_encoders and not self._stop_loop:
            self._move_to_encoders(end_encoders)
            time.sleep(0.6)

    def run_cli(self) -> None:
        self.gripper.init()
        self.print_menu()

        while True:
            cmd = input("\ncmd> ").strip()
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
                self.capture_start()
            elif cmd == "2":
                self.capture_end()
            elif cmd == "r":
                self.start_record()
            elif cmd == "c":
                self.stop_record()
            elif cmd == "o":
                self.add_gripper_open()
            elif cmd == "k":
                self.add_gripper_close()
            elif cmd == "w":
                self.add_wait_event()
            elif cmd == "x":
                self.clear_items()
            elif cmd == "s":
                self.save()
            elif cmd == "l":
                try:
                    self.load()
                except Exception as e:
                    print(f"Load failed: {e}")
            elif cmd == "t":
                self.run_once()
            elif cmd == "y":
                self.run_loop()
            elif cmd == "e":
                self.stop_loop()
            elif cmd == "q":
                self.stop_record()
                self.stop_loop()
                break
            else:
                print("Unknown command. Press 'h' for help.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="myCobot320Pi teaching pick-loop tool")
    parser.add_argument("--port", type=str, default="", help="Serial port, e.g. /dev/ttyAMA0")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--task", type=str, default=DEFAULT_TASK_FILE, help="Task JSON path")
    parser.add_argument("--speed", type=int, default=70, help="Default replay speed [1..100]")
    parser.add_argument("--sample-dt", type=float, default=0.05, help="Record sample interval (seconds)")
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
            sample_dt=args.sample_dt,
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
