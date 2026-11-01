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
import contextlib
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
    """macOS blocks LAN access for non-Apple binaries. Apple's nc is allowed, so pipe ADB through it.

    Every `nc` child is killed and reaped when its client closes or when close() is called, so no
    child outlives the command (nc keeps running after stdin EOF while the TV holds the socket open)."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self._closed = False
        self._lock = threading.Lock()
        self._conns = []  # (client socket, nc process, [relay threads])
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.server.settimeout(0.2)  # lets close() stop the accept loop; closing from another thread may not wake accept()
        self.local_port = self.server.getsockname()[1]
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()

    def _accept(self):
        while not self._closed:
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            proc = subprocess.Popen(["/usr/bin/nc", self.host, str(self.port)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            threads = [threading.Thread(target=self._to_tv, args=(client, proc), daemon=True),
                       threading.Thread(target=self._from_tv, args=(client, proc), daemon=True)]
            with self._lock:
                if self._closed:
                    self._reap(client, proc)
                    return
                self._conns.append((client, proc, threads))
            for thread in threads:
                thread.start()

    @staticmethod
    def _reap(client, proc):
        """Stop one connection: wake the relay threads, kill nc and wait for it."""
        try:
            client.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        proc.kill()
        proc.wait()

    @staticmethod
    def _to_tv(client, proc):
        try:
            while True:
                data = client.recv(65536)
                if not data:
                    break
                proc.stdin.write(data)
        except (OSError, ValueError):
            pass
        finally:
            NcRelay._reap(client, proc)  # the client is gone: nothing left to relay, and nc would wait on the TV forever
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _from_tv(client, proc):
        try:
            while True:
                data = os.read(proc.stdout.fileno(), 65536)
                if not data:
                    break
                client.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            NcRelay._reap(client, proc)
            client.close()
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass

    def close(self):
        """Close the listener and kill and reap every nc child. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            conns = list(self._conns)
        self._acceptor.join(2)
        self.server.close()
        for client, proc, threads in conns:
            self._reap(client, proc)
            for thread in threads:
                thread.join(2)


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
    try:
        dev.connect(rsa_keys=[signer()], auth_timeout_s=120)
    except BaseException:
        try:
            dev.close()
        except Exception:
            pass
        raise
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
    try:
        dev = _connect("127.0.0.1", relay.local_port)
    except BaseException:
        relay.close()
        raise
    dev.canvas_relay = relay
    return dev


def disconnect(dev):
    """Close the device and, when ADB goes through the nc relay, the relay and its nc children."""
    try:
        dev.close()
    except Exception:
        pass
    relay = getattr(dev, "canvas_relay", None)
    if relay is not None:
        relay.close()


@contextlib.contextmanager
def tv():
    """Every command talks to the TV inside `with tv() as dev:` so the connection is always closed."""
    dev = connect()
    try:
        yield dev
    finally:
        disconnect(dev)


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
    with tv() as dev:
        for prop in ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release",
                     "ro.build.version.sdk", "ro.build.display.id"):
            print(f"{prop} = {sh(dev, f'getprop {prop}')}")
        print("home = " + current_home(dev))
        print("screensaver = " + sh(dev, "settings get secure screensaver_components"))


def cmd_shot(path):
    with tv() as dev:
        sh(dev, "screencap -p /sdcard/_canvas_shot.png")
        dev.pull("/sdcard/_canvas_shot.png", path)
        sh(dev, "rm /sdcard/_canvas_shot.png")
        print(f"saved {path}")


def current_home(dev):
    out = sh(dev, "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                  "-c android.intent.category.HOME")
    return out.splitlines()[-1].strip() if out else ""


def canvas_is_home_candidate(dev):
    """True if the TV lists Canvas's HomeActivity among the HOME activities (whichever one currently wins)."""
    out = sh(dev, "cmd package query-activities --brief -a android.intent.action.MAIN "
                  "-c android.intent.category.HOME")
    return any(same_component(line, HOME) for line in out.splitlines() if "/" in line and " " not in line.strip())


def installed(dev, pkg):
    return f"package:{pkg}" in sh(dev, f"pm list packages {pkg}").split()


def enabled(dev, pkg):
    return f"package:{pkg}" not in sh(dev, f"pm list packages -d {pkg}").split()


def backup_path(dev):
    return os.path.join(BACKUPS, sh(dev, "settings get secure android_id") + ".json")


def same_component(a, b):
    """`pkg/.Cls` and `pkg/pkg.Cls` name the same activity."""
    def full(component):
        pkg, _, cls = component.strip().partition("/")
        return f"{pkg}/{pkg + cls if cls.startswith('.') else cls}" if cls else pkg
    return full(a) == full(b)


def save_backup(path, data):
    """Write the backup to disk before the caller goes on (temp file + rename, so it is never half-written)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def take_backup(dev):
    """Back up once per TV. Re-installs keep the first backup so `restore` returns to the stock setup."""
    path = backup_path(dev)
    if os.path.exists(path):
        print(f"Keeping the existing backup {os.path.relpath(path, ROOT)}")
        return path
    home = current_home(dev)
    dream = sh(dev, "settings get secure screensaver_components")
    if PKG in home or PKG in dream:
        sys.exit(f"Refusing to back up: this TV already uses Canvas (home: {home or 'none'}, screensaver: {dream}), "
                 "so these are not its stock settings and `restore` would keep Canvas.\n"
                 "Put the stock home screen and screensaver back first (for example "
                 "`tools/canvas sh pm enable com.google.android.apps.tv.launcherx`), then run install again.")
    if "/" not in home or "ResolverActivity" in home:
        sys.exit(f"Refusing to back up: the TV reports no usable home screen (home: {home or 'none'}). "
                 "It may be showing a chooser or have no launcher enabled. "
                 "Pick a home screen on the TV first, then run install again.")
    data = {
        "taken": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": sh(dev, "getprop ro.product.model"),
        "home": home,
        "secure": {k: sh(dev, f"settings get secure {k}") for k in SECURE_KEYS},
        "system": {k: sh(dev, f"settings get system {k}") for k in SYSTEM_KEYS},
        "disabled_by_canvas": [],
    }
    save_backup(path, data)
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
    with tv() as dev:
        path = take_backup(dev)
        print(f"Uploading {os.path.relpath(apk, ROOT)}…")
        push(dev, apk, "/data/local/tmp/canvas.apk")
        out = run(dev, "pm install -r -g /data/local/tmp/canvas.apk")
        sh(dev, "rm -f /data/local/tmp/canvas.apk")
        if "Success" not in out:
            hint = (f"\nThe installed copy was signed with a different key. Remove it first:"
                    f" tools/canvas sh pm uninstall {PKG}") if "UPDATE_INCOMPATIBLE" in out else ""
            sys.exit("Install failed." + hint)
        grant = run(dev, f"pm grant {PKG} android.permission.WRITE_SECURE_SETTINGS")
        if grant and not app_only:  # a successful grant prints nothing
            sys.exit("Canvas could not be granted WRITE_SECURE_SETTINGS, so it cannot set the screensaver. "
                     "The app is installed, but the home screen and screensaver were not changed.")
        run(dev, f"appops set {PKG} WRITE_SETTINGS allow")
        if app_only:
            print("Installed (app only).")
            return

        # Google TV's launcher outranks every other HOME activity, so Canvas can only become Home once the stock
        # launchers are disabled. Check that Canvas is a candidate, disable them, then check Canvas really won.
        if not canvas_is_home_candidate(dev):
            sys.exit("The TV does not list Canvas as a home screen (no HOME activity for "
                     f"{HOME} in `cmd package query-activities`). Nothing was disabled or changed.")
        with open(path) as f:
            backup = json.load(f)
        run(dev, f"cmd package set-home-activity --user 0 {HOME}")
        disabled_now = []
        for pkg in STOCK_HOMES:
            if installed(dev, pkg) and enabled(dev, pkg):
                if pkg not in backup["disabled_by_canvas"]:
                    backup["disabled_by_canvas"].append(pkg)
                save_backup(path, backup)  # on disk before the disable, so a failure part-way can still be undone
                run(dev, f"pm disable-user --user 0 {pkg}")
                disabled_now.append(pkg)
        run(dev, f"cmd package set-home-activity --user 0 {HOME}")
        home = current_home(dev)
        if not same_component(home, HOME):
            for pkg in disabled_now:
                run(dev, f"pm enable {pkg}")
            stuck = [pkg for pkg in disabled_now if not enabled(dev, pkg)]
            backup["disabled_by_canvas"] = [p for p in backup["disabled_by_canvas"] if p not in disabled_now]
            backup["disabled_by_canvas"] += stuck  # still disabled, so `restore` must still know about them
            save_backup(path, backup)
            if stuck:
                sys.exit(f"Canvas did not become the home screen (the TV says: {home or 'nothing'}), and "
                         f"{', '.join(stuck)} could not be re-enabled. Run `tools/canvas restore`.")
            sys.exit(f"Canvas did not become the home screen (the TV says: {home or 'nothing'}). "
                     f"Re-enabled the stock launcher ({', '.join(disabled_now) or 'none had been disabled'}). "
                     "The screensaver and other settings were not changed.")
        run(dev, f"settings put secure screensaver_components {DREAM}")
        run(dev, "settings put secure screensaver_enabled 1")
        run(dev, "settings put secure screensaver_activate_on_sleep 1")
        # A stale home task from the old launcher can open Canvas behind the current app.
        run(dev, "am force-stop com.android.tv.settings")
        run(dev, "input keyevent KEYCODE_HOME")
    time.sleep(3)
    os.makedirs(SHOTS, exist_ok=True)
    cmd_shot(os.path.join(SHOTS, "install-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".png"))
    print("Installed. Press Home on the remote.")


def check_restored(dev, backup, packages):
    """Read the TV back and list everything that is not as the backup says."""
    problems = []
    want = backup.get("home", "")
    got = current_home(dev)
    if not same_component(got, want):
        problems.append(f"the home screen is {got or 'nothing'}, the backup says {want or 'nothing'}")
    for pkg in packages:
        if not enabled(dev, pkg):
            problems.append(f"{pkg} is still disabled")
    for table, values in (("secure", backup["secure"]), ("system", backup["system"])):
        for key, value in values.items():
            got = sh(dev, f"settings get {table} {key}")
            unset = value in ("", "null")
            if (got not in ("", "null")) if unset else (got != value):
                problems.append(f"{table} {key} is {got!r}, the backup says {value!r}")
    return problems


def cmd_restore(args):
    with tv() as dev:
        path = backup_path(dev)
        if not os.path.exists(path):
            sys.exit("No backup for this TV in tools/backups/.")
        with open(path) as f:
            backup = json.load(f)
        # Also re-enable any installed stock home, in case an install stopped before it recorded the change.
        packages = list(dict.fromkeys(backup["disabled_by_canvas"] + [p for p in STOCK_HOMES if installed(dev, p)]))
        for pkg in packages:
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
        problems = check_restored(dev, backup, packages)
        if problems:
            sys.exit("Restore did not take effect on the TV:\n  - " + "\n  - ".join(problems)
                     + "\nThe backup is still in tools/backups/. Run restore again, or fix these on the TV.")
        run(dev, "input keyevent KEYCODE_HOME")
        print("Restored the TV's previous home screen and screensaver.")


def read_list(path):
    with open(path) as f:
        return [line.split("#", 1)[0].strip() for line in f if line.split("#", 1)[0].strip()]


def cmd_debloat(path, enable=False):
    with tv() as dev:
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
        with tv() as dev:
            print(sh(dev, " ".join(args[1:])))
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
