# disable_touchpad

Disable the GNOME touchpad for a short time while typing on an external ThinkPad keyboard.

This project is intended for setups where an external ThinkPad Bluetooth TrackPoint keyboard is used with a laptop, and accidental touchpad input while typing is annoying.

## What It Does

- Watches specific external ThinkPad keyboard event devices using `evdev`
- Temporarily disables the GNOME touchpad while typing
- Re-enables the touchpad automatically after a short delay
- Ignores modifier-heavy input such as `Ctrl`, `Alt`, `Super`, and common navigation keys
- Stays idle when the target keyboard is not connected
- Recovers automatically from keyboard disconnects
- Uses udev hotplug monitoring instead of periodic polling

## Supported Keyboards

- `ThinkPad Bluetooth TrackPoint Keyboard`
- `Primax Electronics Ltd. ThinkPad Bluetooth TrackPoint Keyboard - USB`

## Ignored Keys

The script does not disable the touchpad for:

- `Ctrl`
- `Alt`
- `Super`
- `Shift`
- `Tab`
- `Escape`
- `Enter`
- `Backspace`

It also does not disable the touchpad when another key is pressed while `Ctrl`, `Alt`, or `Super` is held.

## Main Script

- [`disable_touchpad_on_keypress.py`](./disable_touchpad_on_keypress.py)

## Requirements

- Linux
- GNOME
- `systemd`
- Python 3
- Python packages: `evdev`, `pyudev`
- Access to `/dev/input/event*`

## Run Manually

```bash
./disable_touchpad_on_keypress.py
```

Optional arguments:

```bash
./disable_touchpad_on_keypress.py --user YOUR_USERNAME --delay 0.4
```

## Install As A System Service

Install the script:

```bash
sudo install -m 755 disable_touchpad_on_keypress.py /usr/local/bin/disable_touchpad_on_keypress
```

Create `/etc/systemd/system/x1_fold_disable_touchpad_on_keypress.service`:

```ini
[Unit]
Description=Disable touchpad while typing on external ThinkPad keyboard
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/local/bin/disable_touchpad_on_keypress --user YOUR_USERNAME
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now x1_fold_disable_touchpad_on_keypress.service
```

View status and logs:

```bash
sudo systemctl status x1_fold_disable_touchpad_on_keypress.service
journalctl -u x1_fold_disable_touchpad_on_keypress.service -f
```

## Uninstall

Stop and disable the service:

```bash
sudo systemctl disable --now x1_fold_disable_touchpad_on_keypress.service
```

Remove the service file and installed script:

```bash
sudo rm /etc/systemd/system/x1_fold_disable_touchpad_on_keypress.service
sudo rm /usr/local/bin/disable_touchpad_on_keypress
sudo systemctl daemon-reload
```

## Notes

- If the service runs as `root`, pass `--user YOUR_USERNAME` so `gsettings` is applied to the correct desktop session.
- The script caches touchpad state and only calls `gsettings` when a change is needed.
- If your environment uses different device names, update `TARGET_KEYBOARD_NAMES` in the script.
