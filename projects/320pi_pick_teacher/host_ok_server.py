#!/usr/bin/env python3
"""
Minimal TCP host server for myCobot320Pi handshake testing.

Default behavior:
- Listen on 0.0.0.0:9001
- Receive one newline-terminated message from the robot
- Reply with "OK\\n"

It can also run in manual mode, where each reply is confirmed from stdin.
"""

import argparse
import signal
import socket
import time
from typing import Optional


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9001
DEFAULT_REPLY = "OK"
DEFAULT_REPLY_DELAY = 5.0
SERVER_POLL_INTERVAL = 1.0


STOP_REQUESTED = False


def log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S", time.localtime())
    print(f"[{stamp}] {message}")


def request_stop(_signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    log("Stop requested. Shutting down...")


def recv_line(conn: socket.socket) -> Optional[str]:
    chunks = bytearray()
    while True:
        data = conn.recv(1)
        if not data:
            if not chunks:
                return None
            raise RuntimeError("Client closed the socket mid-message.")
        if data == b"\n":
            return chunks.decode("utf-8", errors="replace").rstrip("\r")
        chunks.extend(data)


def send_line(conn: socket.socket, message: str) -> None:
    conn.sendall((message.rstrip("\r\n") + "\n").encode("utf-8"))


def run_server(host: str, port: int, reply: str, manual: bool, reply_delay: float) -> int:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        server.settimeout(SERVER_POLL_INTERVAL)
        log(f"Listening on {host}:{port}")
        log("Press Ctrl+C to stop.")

        while not STOP_REQUESTED:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(None)
                client_host, client_port = addr[0], addr[1]
                log(f"Client connected: {client_host}:{client_port}")

                try:
                    while not STOP_REQUESTED:
                        message = recv_line(conn)
                        if message is None:
                            log("Client disconnected.")
                            break

                        log(f"Received: {message!r}")

                        if manual:
                            user_input = input("Reply now? [Enter=OK / q=close / custom text]: ").strip()
                            if user_input.lower() == "q":
                                log("Closing client connection by operator request.")
                                break
                            reply_to_send = reply if user_input == "" else user_input
                        else:
                            reply_to_send = reply

                        if reply_delay > 0:
                            log(f"Reply delay: waiting {reply_delay:.1f}s before sending.")
                            time.sleep(reply_delay)
                        log(f"Sending: {reply_to_send!r}")
                        send_line(conn, reply_to_send)

                except KeyboardInterrupt:
                    request_stop(None, None)
                except Exception as exc:
                    log(f"Connection error: {exc}")
                finally:
                    log(f"Client closed: {client_host}:{client_port}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal TCP host server for myCobot320Pi handshake")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Listen address, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Listen port, default: 9001")
    parser.add_argument("--reply", default=DEFAULT_REPLY, help="Reply text, default: OK")
    parser.add_argument(
        "--reply-delay",
        type=float,
        default=DEFAULT_REPLY_DELAY,
        help="Seconds to wait after receiving a message before replying, default: 5.0",
    )
    parser.add_argument("--manual", action="store_true", help="Wait for console confirmation before replying")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGINT, request_stop)
    try:
        return run_server(args.host, args.port, args.reply, args.manual, max(0.0, args.reply_delay))
    except KeyboardInterrupt:
        log("Server stopped.")
        return 130
    except Exception as exc:
        log(f"Fatal error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
