#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import pwd
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

import pyudev
from evdev import InputDevice, ecodes


DEFAULT_DELAY = 0.4
LOG_TOUCHPAD_STATE_CHANGES = False
TARGET_KEYBOARD_NAMES = {
    "ThinkPad Bluetooth TrackPoint Keyboard",
    "Primax Electronics Ltd. ThinkPad Bluetooth TrackPoint Keyboard - USB",
}
TARGET_KEYBOARD_NAMES_QUOTED = {f'"{name}"' for name in TARGET_KEYBOARD_NAMES}
COMBO_SUPPRESS_KEYS = {
    ecodes.KEY_LEFTCTRL,
    ecodes.KEY_RIGHTCTRL,
    ecodes.KEY_LEFTALT,
    ecodes.KEY_RIGHTALT,
    ecodes.KEY_LEFTMETA,
    ecodes.KEY_RIGHTMETA,
}
IGNORED_KEYS = COMBO_SUPPRESS_KEYS | {
    ecodes.KEY_LEFTSHIFT,
    ecodes.KEY_RIGHTSHIFT,
    ecodes.KEY_TAB,
    ecodes.KEY_ESC,
    ecodes.KEY_ENTER,
    ecodes.KEY_BACKSPACE,
}
RECOVERABLE_DEVICE_ERRNOS = {errno.ENODEV, errno.EIO, errno.EBADF}


def log(message: str) -> None:
    now = time.strftime("%H:%M:%S")
    millis = int((time.time() % 1) * 1000)
    print(f"[{now}.{millis:03d}] {message}", flush=True)


def detect_target_user() -> str:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user

    sessions = subprocess.run(
        ["loginctl", "list-sessions", "--no-legend"],
        check=True,
        capture_output=True,
        text=True,
    )

    for line in sessions.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue

        session = subprocess.run(
            [
                "loginctl",
                "show-session",
                parts[0],
                "--property=Active",
                "--property=State",
                "--property=Name",
                "--property=Class",
                "--value",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        values = session.stdout.splitlines()
        if len(values) != 4:
            continue

        active, state, name, session_class = values
        if active == "yes" and state == "active" and session_class == "user" and name and name != "root":
            return name

    raise RuntimeError("could not determine active desktop user")


@dataclass
class TouchpadController:
    target_user: str
    state: str | None = None

    def __post_init__(self) -> None:
        pw = pwd.getpwnam(self.target_user)
        self.uid = pw.pw_uid
        self.gid = pw.pw_gid
        self.runtime_dir = f"/run/user/{pw.pw_uid}"
        self.bus = f"unix:path={self.runtime_dir}/bus"

    def _demote(self) -> None:
        os.initgroups(self.target_user, self.gid)
        os.setgid(self.gid)
        os.setuid(self.uid)

    def set_state(self, value: str) -> bool:
        if value == self.state:
            return True

        try:
            subprocess.run(
                [
                    "gsettings",
                    "set",
                    "org.gnome.desktop.peripherals.touchpad",
                    "send-events",
                    value,
                ],
                check=True,
                env={
                    **os.environ,
                    "XDG_RUNTIME_DIR": self.runtime_dir,
                    "DBUS_SESSION_BUS_ADDRESS": self.bus,
                },
                preexec_fn=self._demote,
            )
        except subprocess.CalledProcessError as exc:
            self.state = None
            log(f"failed to set touchpad {value}: {exc}")
            return False

        self.state = value
        if LOG_TOUCHPAD_STATE_CHANGES:
            log(f"touchpad {value}")
        return True


def find_target_keyboard_paths(context: pyudev.Context) -> list[str]:
    paths: list[str] = []

    for device in context.list_devices(subsystem="input"):
        node = device.device_node
        if not node or not node.startswith("/dev/input/event"):
            continue
        if device.properties.get("ID_INPUT_KEYBOARD") != "1":
            continue

        parent_name = device.parent.properties.get("NAME") if device.parent else None
        if parent_name not in TARGET_KEYBOARD_NAMES_QUOTED:
            continue

        paths.append(node)

    return sorted(set(paths))


@dataclass
class KeyboardWatcher:
    selector: selectors.BaseSelector
    controller: TouchpadController
    udev_context: pyudev.Context = field(default_factory=pyudev.Context)
    devices: list[InputDevice] = field(default_factory=list)
    modifiers_down: set[int] = field(default_factory=set)
    watching_logged: bool = False

    def reconcile(self) -> None:
        wanted_paths = find_target_keyboard_paths(self.udev_context)
        current_paths = sorted(device.path for device in self.devices)
        if current_paths == wanted_paths:
            return

        self.close_devices()
        for path in wanted_paths:
            try:
                device = InputDevice(path)
            except OSError as exc:
                log(f"failed to open {path}: {exc}")
                continue
            self.selector.register(device, selectors.EVENT_READ, data="keyboard")
            self.devices.append(device)

        self.modifiers_down.clear()
        self.controller.set_state("enabled")

        if self.devices:
            watched = ", ".join(f"{device.path} ({device.name})" for device in self.devices)
            log(f"watching keyboards: {watched}")
            self.watching_logged = True
            return

        if self.watching_logged:
            names = ", ".join(sorted(repr(name) for name in TARGET_KEYBOARD_NAMES))
            log(f"none of the target keyboards detected; staying idle: {names}")
        self.watching_logged = False

    def close_devices(self) -> None:
        for device in self.devices:
            try:
                self.selector.unregister(device)
            except Exception:
                pass
            device.close()
        self.devices.clear()

    def handle_device_failure(self, device: InputDevice, exc: OSError) -> None:
        if exc.errno in RECOVERABLE_DEVICE_ERRNOS:
            log(f"lost keyboard {device.path}: {exc}")
        else:
            log(f"read failure on {device.path}: {exc}")
        self.close_devices()
        self.modifiers_down.clear()
        self.watching_logged = False
        self.controller.set_state("enabled")

    def should_disable_for(self, keycode: int, value: int) -> bool:
        if keycode in COMBO_SUPPRESS_KEYS:
            if value == 0:
                self.modifiers_down.discard(keycode)
            elif value == 1:
                self.modifiers_down.add(keycode)
            return False

        if value != 1:
            return False
        if keycode in IGNORED_KEYS:
            return False
        if self.modifiers_down:
            return False
        return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="Desktop session user")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Seconds to wait before re-enabling the touchpad",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_user = args.user or detect_target_user()
    log(f"using desktop user: {target_user}")

    controller = TouchpadController(target_user)
    monitor = pyudev.Monitor.from_netlink(pyudev.Context())
    monitor.filter_by(subsystem="input")
    monitor.start()

    selector = selectors.DefaultSelector()
    selector.register(monitor, selectors.EVENT_READ, data="udev")

    watcher = KeyboardWatcher(selector=selector, controller=controller)
    reenable_deadline: float | None = None
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        watcher.reconcile()

        while running:
            timeout = None
            if reenable_deadline is not None:
                timeout = max(0.0, reenable_deadline - time.monotonic())

            try:
                ready = selector.select(timeout)
            except OSError as exc:
                log(f"selector failure: {exc}")
                watcher.close_devices()
                watcher.modifiers_down.clear()
                reenable_deadline = None
                controller.set_state("enabled")
                continue

            if not ready:
                if reenable_deadline is not None and time.monotonic() >= reenable_deadline:
                    controller.set_state("enabled")
                    reenable_deadline = None
                continue

            for key, _mask in ready:
                if key.data == "udev":
                    while monitor.poll(timeout=0) is not None:
                        pass
                    watcher.reconcile()
                    reenable_deadline = None
                    continue

                device: InputDevice = key.fileobj
                try:
                    events = list(device.read())
                except OSError as exc:
                    watcher.handle_device_failure(device, exc)
                    reenable_deadline = None
                    break

                for event in events:
                    if event.type != ecodes.EV_KEY:
                        continue
                    if watcher.should_disable_for(event.code, event.value):
                        controller.set_state("disabled")
                        reenable_deadline = time.monotonic() + args.delay
    finally:
        watcher.close_devices()
        selector.unregister(monitor)
        controller.set_state("enabled")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)
