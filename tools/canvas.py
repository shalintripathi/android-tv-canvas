#!/usr/bin/env python3
"""Canvas TV tool: talk to an Android TV over Wi-Fi ADB.

    tools/canvas find                      scan the Wi-Fi, remember the first TV with ADB open
    tools/canvas probe                     model and Android version
    tools/canvas sh '<command>'            run a shell command on the TV
    tools/canvas shot out.png              screenshot
    tools/canvas install [apk] [--app-only]
                                           install Canvas; without --app-only also make it the
                                           home screen and screensaver (backs up first)
    tools/canvas restore [--uninstall]     undo `install` from the backup
    tools/canvas debloat <list>            disable the packages in a list (reversible)
    tools/canvas undebloat <list>          re-enable them

The TV address comes from $TV_HOST or tools/.tv_host (written by `find`).
"""
import datetime
import errno
import hashlib
import json
import os
import platform
import select
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import TcpTimeoutException
from adb_shell.transport.tcp_transport import TcpTransport

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST_FILE = os.path.join(HERE, ".tv_host")
KEY = os.path.join(HERE, "adbkey", "adbkey")
BACKUPS = os.path.join(HERE, "backups")
SHOTS = os.path.join(ROOT, "shots")

PKG = "io.github.shalintripathi.canvas"
HOME = PKG + "/.home.HomeActivity"
DREAM = PKG + "/.frame.FrameDreamService"
STOCK_HOMES = ["com.google.android.apps.tv.launcherx", "com.google.android.tungsten.setupwraith"]
SECURE_KEYS = ["screensaver_components", "screensaver_enabled", "screensaver_activate_on_sleep", "sleep_timeout"]
SYSTEM_KEYS = ["screen_off_timeout"]
ADB_PORT = 5555


# ---------------------------------------------------------------- connection

def _send_all(self, data, transport_timeout_s):
    """adb-shell sends with one send() on a non-blocking socket and ignores short writes, so anything
    bigger than the OS send buffer (128 KB on macOS) is silently cut off and `push` hangs. Send it all."""
    view = memoryview(data)
    while view:
        _, writeable, _ = select.select([], [self._connection], [], transport_timeout_s)
        if not writeable:
            raise TcpTimeoutException(f"Sending data to {self._host}:{self._port} timed out "
                                      f"after {transport_timeout_s} seconds.")
        try:
            view = view[self._connection.send(view):]
        except BlockingIOError:
            continue
    return len(data)


TcpTransport.bulk_write = _send_all


class NcRelay:
    """macOS blocks LAN access for non-Apple binaries. Apple's nc is allowed, so pipe ADB through it."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.local_port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            proc = subprocess.Popen(["/usr/bin/nc", self.host, str(self.port)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            threading.Thread(target=self._to_tv, args=(client, proc), daemon=True).start()
            threading.Thread(target=self._from_tv, args=(client, proc), daemon=True).start()

    @staticmethod
    def _to_tv(client, proc):
        try:
            while True:
                data = client.recv(65536)
                if not data:
                    break
                proc.stdin.write(data)
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    @staticmethod
    def _from_tv(client, proc):
        try:
            while True:
                data = os.read(proc.stdout.fileno(), 65536)
                if not data:
                    break
                client.sendall(data)
        except OSError:
            pass
        finally:
            client.close()
            proc.kill()


def tv_host():
    host = os.environ.get("TV_HOST")
    if not host and os.path.exists(HOST_FILE):
        host = open(HOST_FILE).read().strip()
    if not host:
        sys.exit("Don't know your TV's address. Run `tools/canvas find`, or set TV_HOST=192.168.x.y")
    return host


def signer():
    os.makedirs(os.path.dirname(KEY), exist_ok=True)
    if not os.path.exists(KEY):
        keygen(KEY)
    with open(KEY) as private, open(KEY + ".pub") as public:
        return PythonRSASigner(public.read(), private.read())


def _connect(host, port):
    dev = AdbDeviceTcp(host, port, default_transport_timeout_s=60.0)
    dev.connect(rsa_keys=[signer()], auth_timeout_s=120)
    return dev


def connect():
    host, port = tv_host(), int(os.environ.get("TV_PORT", ADB_PORT))
    print(f"Connecting to {host}… (accept \"Allow USB debugging?\" on the TV if it asks)", file=sys.stderr)
    try:
        return _connect(host, port)
    except OSError as e:
        if platform.system() != "Darwin" or e.errno not in (errno.EHOSTUNREACH, errno.ENETUNREACH):
            raise
    relay = NcRelay(host, port)
    return _connect("127.0.0.1", relay.local_port)


def sh(dev, command):
    return dev.shell(command, read_timeout_s=120).strip()


def run(dev, command):
    """One command per shell call: chained `pm` commands can silently stop after the first."""
    print(f"$ {command}")
    out = sh(dev, command)
    if out:
        print(out)
    return out


def push(dev, local, remote):
    dev.push(local, remote, read_timeout_s=300)
    remote_md5 = sh(dev, f"md5sum {remote}").split()[0]
    with open(local, "rb") as f:
        local_md5 = hashlib.md5(f.read()).hexdigest()
    if remote_md5 != local_md5:
        sys.exit(f"Upload corrupted: md5 {remote_md5} != {local_md5}")


# ---------------------------------------------------------------- commands

def cmd_find():
    me = local_ip()
    net = me.rsplit(".", 1)[0]
    print(f"Scanning {net}.0/24 from {me}…")
    names = mdns_names()
    if names:
        print("Android TVs announcing themselves: " + ", ".join(names))
    with ThreadPoolExecutor(64) as pool:
        hits = [ip for ip in pool.map(lambda i: f"{net}.{i}" if port_open(f"{net}.{i}", ADB_PORT) else None,
                                      range(1, 255)) if ip]
    if not hits:
        sys.exit("No TV with ADB open. On the TV: Developer options → USB debugging, then run this again.")
    print("ADB open on: " + ", ".join(hits))
    with open(HOST_FILE, "w") as f:
        f.write(hits[0])
    print(f"Saved {hits[0]}. Next: tools/canvas probe")


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def port_open(ip, port):
    args = ["nc", "-z", "-w", "1"] + (["-G", "1"] if platform.system() == "Darwin" else []) + [ip, str(port)]
    return subprocess.run(args, capture_output=True).returncode == 0


def mdns_names():
    """Names of Android TVs on the network (macOS only; dns-sd never exits on its own)."""
    if platform.system() != "Darwin":
        return []
    try:
        out = subprocess.run(["dns-sd", "-B", "_androidtvremote2._tcp", "local."],
                             capture_output=True, text=True, timeout=3).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    return sorted({" ".join(line.split()[6:]) for line in out.splitlines() if " Add " in line})


def cmd_probe():
    dev = connect()
    for prop in ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release",
                 "ro.build.version.sdk", "ro.build.display.id"):
        print(f"{prop} = {sh(dev, f'getprop {prop}')}")
    print("home = " + current_home(dev))
    print("screensaver = " + sh(dev, "settings get secure screensaver_components"))


def cmd_shot(path):
    dev = connect()
    sh(dev, "screencap -p /sdcard/_canvas_shot.png")
    dev.pull("/sdcard/_canvas_shot.png", path)
    sh(dev, "rm /sdcard/_canvas_shot.png")
    print(f"saved {path}")


def current_home(dev):
    out = sh(dev, "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                  "-c android.intent.category.HOME")
    return out.splitlines()[-1].strip() if out else ""


def installed(dev, pkg):
    return f"package:{pkg}" in sh(dev, f"pm list packages {pkg}").split()


def enabled(dev, pkg):
    return f"package:{pkg}" not in sh(dev, f"pm list packages -d {pkg}").split()


def backup_path(dev):
    return os.path.join(BACKUPS, sh(dev, "settings get secure android_id") + ".json")


def take_backup(dev):
    """Back up once per TV. Re-installs keep the first backup so `restore` returns to the stock setup."""
    path = backup_path(dev)
    if os.path.exists(path):
        print(f"Keeping the existing backup {os.path.relpath(path, ROOT)}")
        return path
    data = {
        "taken": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": sh(dev, "getprop ro.product.model"),
        "home": current_home(dev),
        "secure": {k: sh(dev, f"settings get secure {k}") for k in SECURE_KEYS},
        "system": {k: sh(dev, f"settings get system {k}") for k in SYSTEM_KEYS},
        "disabled_by_canvas": [],
    }
    os.makedirs(BACKUPS, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Backed up the TV's settings to {os.path.relpath(path, ROOT)}")
    return path


def default_apk():
    for candidate in ("app/build/outputs/apk/release/app-release.apk",
                      "app/build/outputs/apk/debug/app-debug.apk", "build/ci/app-debug.apk"):
        path = os.path.join(ROOT, candidate)
        if os.path.exists(path):
            return path
    sys.exit("No APK found. Run tools/fetch-ci-apk.sh or pass the APK path.")


def cmd_install(args):
    apk = next((a for a in args if not a.startswith("--")), None) or default_apk()
    app_only = "--app-only" in args
    dev = connect()
    path = take_backup(dev)
    print(f"Uploading {os.path.relpath(apk, ROOT)}…")
    push(dev, apk, "/data/local/tmp/canvas.apk")
    out = run(dev, "pm install -r -g /data/local/tmp/canvas.apk")
    sh(dev, "rm -f /data/local/tmp/canvas.apk")
    if "Success" not in out:
        hint = (f"\nThe installed copy was signed with a different key. Remove it first:"
                f" tools/canvas sh pm uninstall {PKG}") if "UPDATE_INCOMPATIBLE" in out else ""
        sys.exit("Install failed." + hint)
    run(dev, f"pm grant {PKG} android.permission.WRITE_SECURE_SETTINGS")
    run(dev, f"appops set {PKG} WRITE_SETTINGS allow")
    if app_only:
        print("Installed (app only).")
        return

    with open(path) as f:
        backup = json.load(f)
    run(dev, f"cmd package set-home-activity --user 0 {HOME}")
    for pkg in STOCK_HOMES:
        if installed(dev, pkg) and enabled(dev, pkg):
            run(dev, f"pm disable-user --user 0 {pkg}")
            if pkg not in backup["disabled_by_canvas"]:
                backup["disabled_by_canvas"].append(pkg)
    with open(path, "w") as f:
        json.dump(backup, f, indent=2)
    run(dev, f"cmd package set-home-activity --user 0 {HOME}")
    run(dev, f"settings put secure screensaver_components {DREAM}")
    run(dev, "settings put secure screensaver_enabled 1")
    run(dev, "settings put secure screensaver_activate_on_sleep 1")
    # A stale home task from the old launcher can open Canvas behind the current app.
    run(dev, "am force-stop com.android.tv.settings")
    run(dev, "input keyevent KEYCODE_HOME")
    time.sleep(3)
    dev.close()
    os.makedirs(SHOTS, exist_ok=True)
    cmd_shot(os.path.join(SHOTS, "install-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".png"))
    print("Installed. Press Home on the remote.")


def cmd_restore(args):
    dev = connect()
    path = backup_path(dev)
    if not os.path.exists(path):
        sys.exit("No backup for this TV in tools/backups/.")
    with open(path) as f:
        backup = json.load(f)
    for pkg in backup["disabled_by_canvas"]:
        run(dev, f"pm enable {pkg}")
    for table, values in (("secure", backup["secure"]), ("system", backup["system"])):
        for key, value in values.items():
            run(dev, f"settings delete {table} {key}" if value in ("", "null")
                else f"settings put {table} {key} {value}")
    home = backup.get("home", "")
    if "/" in home and not home.startswith(PKG):
        run(dev, f"cmd package set-home-activity --user 0 {home}")
    if "--uninstall" in args:
        run(dev, f"pm uninstall {PKG}")
    run(dev, "input keyevent KEYCODE_HOME")
    print("Restored the TV's previous home screen and screensaver.")


def read_list(path):
    with open(path) as f:
        return [line.split("#", 1)[0].strip() for line in f if line.split("#", 1)[0].strip()]


def cmd_debloat(path, enable=False):
    dev = connect()
    for pkg in read_list(path):
        run(dev, f"pm enable {pkg}" if enable else f"pm disable-user --user 0 {pkg}")


def main():
    args = sys.argv[1:]
    command = args[0] if args else "help"
    if command == "find":
        cmd_find()
    elif command == "probe":
        cmd_probe()
    elif command == "sh" and len(args) > 1:
        print(sh(connect(), " ".join(args[1:])))
    elif command == "shot" and len(args) > 1:
        cmd_shot(args[1])
    elif command == "install":
        cmd_install(args[1:])
    elif command == "restore":
        cmd_restore(args[1:])
    elif command == "debloat" and len(args) > 1:
        cmd_debloat(args[1])
    elif command == "undebloat" and len(args) > 1:
        cmd_debloat(args[1], enable=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
