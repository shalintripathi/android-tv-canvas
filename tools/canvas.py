#!/usr/bin/env python3
"""Canvas TV tool: talk to an Android TV over Wi-Fi ADB.

    tools/canvas find                      scan the Wi-Fi, remember the first TV with ADB open
    tools/canvas probe                     model and Android version
    tools/canvas sh '<command>'            run a shell command on the TV
    tools/canvas shot out.png              screenshot
    tools/canvas install [apk] [--app-only] [--yes]
                                           install Canvas; without --app-only also make it the
                                           home screen and screensaver. It backs up first, lists
                                           the TV's own home app(s) and asks before disabling
                                           them (--yes answers for you). Only after a yes does it
                                           grant Canvas its two settings permissions. --app-only
                                           installs or updates the app and grants nothing
                                           (grants from an earlier install survive an update)
    tools/canvas restore [--uninstall]     undo `install` from the backup, taking Canvas's two
                                           permissions back; --uninstall also removes Canvas and
                                           the photos added to it
    tools/canvas debloat <list> [--yes | --group "<name>"]
                                           disable packages from a device list (reversible). The list groups
                                           packages under `## Name` lines; each group is shown and you are
                                           asked about it (default No). --yes applies every group without
                                           asking, --group applies just that one.
    tools/canvas undebloat <list> [--yes | --group "<name>"]
                                           the same, re-enabling

The TV address comes from $TV_HOST or tools/.tv_host (written by `find`).
"""
import contextlib
import datetime
import errno
import hashlib
import json
import os
import platform
import re
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
# Android 10+ tells an app the Wi-Fi network's name only if it holds location. Canvas shows the name in its header.
LOCATION = ["android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION"]
# Home apps that `install` never disables: Canvas itself, and the system Settings apps, whose FallbackHome is
# the TV's safety net when no other home app is enabled. Any other package with a FallbackHome (a vendor's own
# Settings, say) is kept too: see stock_homes.
KEEP_HOMES = [PKG, "com.android.tv.settings", "com.android.settings"]
SAFETY_NET_CLASS = "FallbackHome"
# The two home apps older versions of `install` disabled by name. Only `restore` still uses this list, to
# re-enable them on a TV where an old install recorded nothing.
LEGACY_STOCK_HOMES = ["com.google.android.apps.tv.launcherx", "com.google.android.tungsten.setupwraith"]
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
        sys.exit("Don't know your TV's address. Run `tools/canvas find`, or set TV_HOST=<tv-ip>")
    return host


def private_only(path, mode):
    """Set `path` to `mode` unless it already is (a key made by an older version may be readable by everyone)."""
    if os.stat(path).st_mode & 0o777 != mode:
        os.chmod(path, mode)


def signer():
    """The ADB key in tools/adbkey/. Whoever can read it can drive the TV, so only you can: folder 0700, key 0600."""
    folder = os.path.dirname(KEY)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    private_only(folder, 0o700)
    if not os.path.exists(KEY):
        keygen(KEY)
    private_only(KEY, 0o600)
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


def home_components(dev):
    """Every HOME activity the TV lists, as `pkg/.Cls` strings in the order the TV gives them (whichever one
    currently wins). `--brief` prints a count line and a priority line around each component; those are skipped."""
    out = sh(dev, "cmd package query-activities --brief -a android.intent.action.MAIN "
                  "-c android.intent.category.HOME")
    return [line.strip() for line in out.splitlines() if "/" in line and " " not in line.strip()]


def stock_homes(components):
    """The packages `install` has to disable before Canvas can be Home: the package of every HOME activity,
    except KEEP_HOMES and any package that has a FallbackHome among its HOME activities (that class is the system's
    safety net when no other home app works, and `pm disable-user` would switch off the rest of the package with it).
    In the order listed, each package once."""
    safety_nets = {component.partition("/")[0] for component in components
                   if component.partition("/")[2].endswith(SAFETY_NET_CLASS)}
    packages = []
    for component in components:
        pkg = component.partition("/")[0]
        if pkg not in KEEP_HOMES and pkg not in safety_nets and pkg not in packages:
            packages.append(pkg)
    return packages


def installed(dev, pkg):
    return f"package:{pkg}" in sh(dev, f"pm list packages {pkg}").split()


def enabled(dev, pkg):
    return f"package:{pkg}" not in sh(dev, f"pm list packages -d {pkg}").split()


def disabled_packages(dev):
    """Every package the TV has switched off, from the `package:<name>` lines of `pm list packages -d`."""
    lines = (line.strip() for line in sh(dev, "pm list packages -d").splitlines())
    return [line[len("package:"):] for line in lines if line.startswith("package:")]


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
                 "Put the stock home screen and screensaver back first (list what is switched off with "
                 "`tools/canvas sh 'pm list packages -d'`, then `tools/canvas sh pm enable <package>`), "
                 "then run install again.")
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
        "disabled_before": disabled_packages(dev),  # off before Canvas came: `restore` leaves these off
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


INSTALL_USAGE = "usage: tools/canvas install [apk] [--app-only] [--yes]"


def parse_install_args(args):
    """(apk path or None, app only?, answer yes to the question?). Rejects anything else."""
    apk, app_only, yes = None, False, False
    for arg in args:
        if arg == "--app-only":
            app_only = True
        elif arg == "--yes":
            yes = True
        elif arg.startswith("-"):
            sys.exit(f"Unknown option {arg}\n" + INSTALL_USAGE)
        elif apk is None:
            apk = arg
        else:
            sys.exit(f"Unexpected argument {arg!r}\n" + INSTALL_USAGE)
    return apk, app_only, yes


def confirm_switch_off(stock, yes, ask):
    """Show the TV's own home apps that are about to be disabled and ask. Anything but y or yes, end of input
    (Ctrl-D) and Ctrl-C all stop the install before anything is switched off. Nothing to disable: no question."""
    if not stock:
        print("The TV has no other home app to switch off.")
        return
    print("These apps offer themselves as the TV's home screen. To make Canvas the home screen they have to be "
          "switched off (reversible with `tools/canvas restore`):")
    for pkg in stock:
        print(f"  {pkg}")
    if yes:
        return
    try:
        answer = ask("Disable these and make Canvas the home screen? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        sys.exit("\nStopped. Canvas is installed, but nothing else on the TV was changed: it was not granted the "
                 "location or settings permissions, and the home screen, the home app(s) above and the screensaver "
                 "are as they were. "
                 "Run the install again and answer y, or add --yes, to go on.")


def take_permissions_back(dev):
    """Undo every grant make_canvas_home makes. Revoking location also stops Canvas, if it is running."""
    run(dev, f"pm revoke {PKG} android.permission.WRITE_SECURE_SETTINGS")
    run(dev, f"appops set {PKG} WRITE_SETTINGS default")
    for permission in LOCATION:
        run(dev, f"pm revoke {PKG} {permission}")


def make_canvas_home(dev, path, stock):
    """After the yes: grant the permissions, switch the TV's own home apps off, check that Canvas won, and only
    then set the screensaver. If Canvas did not win, the apps switched off by this run are switched back on and
    the permissions are taken back."""
    grant = run(dev, f"pm grant {PKG} android.permission.WRITE_SECURE_SETTINGS")
    if grant:  # a successful grant prints nothing
        sys.exit("Canvas could not be granted WRITE_SECURE_SETTINGS, so it cannot set the screensaver. "
                 "The app is installed, but the home screen and screensaver were not changed.")
    run(dev, f"appops set {PKG} WRITE_SETTINGS allow")
    for permission in LOCATION:
        if run(dev, f"pm grant {PKG} {permission}"):
            print("Canvas could not be granted location, so its header will not show the Wi-Fi network's name.")
    with open(path) as f:
        backup = json.load(f)
    run(dev, f"cmd package set-home-activity --user 0 {HOME}")
    disabled_now = []
    for pkg in stock:
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
        take_permissions_back(dev)
        if stuck:
            sys.exit(f"Canvas did not become the home screen (the TV says: {home or 'nothing'}), and "
                     f"{', '.join(stuck)} could not be re-enabled. Run `tools/canvas restore`.")
        sys.exit(f"Canvas did not become the home screen (the TV says: {home or 'nothing'}). "
                 f"Re-enabled the TV's own home app ({', '.join(disabled_now) or 'none had been disabled'}) and "
                 "took back Canvas's permissions. The screensaver and other settings were not changed.")
    run(dev, f"settings put secure screensaver_components {DREAM}")
    run(dev, "settings put secure screensaver_enabled 1")
    run(dev, "settings put secure screensaver_activate_on_sleep 1")
    # A stale home task from the old launcher can open Canvas behind the current app.
    run(dev, "am force-stop com.android.tv.settings")
    run(dev, "input keyevent KEYCODE_HOME")


def cmd_install(args, ask=None):
    ask = ask or input
    apk, app_only, yes = parse_install_args(args)
    apk = apk or default_apk()
    with tv() as dev:
        path = take_backup(dev)
        print(f"Uploading {os.path.relpath(apk, ROOT)}…")
        push(dev, apk, "/data/local/tmp/canvas.apk")
        out = run(dev, "pm install -r /data/local/tmp/canvas.apk")  # no -g: permissions only after the yes
        sh(dev, "rm -f /data/local/tmp/canvas.apk")
        if "Success" not in out:
            hint = ("\nThe installed copy was signed with a different key (debug and release builds are). To switch "
                    "builds, run `tools/canvas restore --uninstall`, then `tools/canvas install <apk>`. Uninstalling "
                    "deletes the photos added from phones.") if "UPDATE_INCOMPATIBLE" in out else ""
            sys.exit("Install failed." + hint)
        if app_only:  # grants nothing: the ones an earlier full install made survive `pm install -r`
            print("Installed (app only).")
            return

        # A TV's own launcher usually outranks every other HOME activity (Google TV's always does), so Canvas can
        # only become Home once those launchers are disabled. Ask the TV which HOME activities it has, check that
        # Canvas is one of them, ask the owner, disable the others, then check Canvas really won.
        components = home_components(dev)
        if not any(same_component(component, HOME) for component in components):
            sys.exit("The TV does not list Canvas as a home screen (no HOME activity for "
                     f"{HOME} in `cmd package query-activities`). Nothing was disabled or changed.")
        stock = stock_homes(components)
        confirm_switch_off(stock, yes, ask)
        try:
            make_canvas_home(dev, path, stock)
        except (Exception, KeyboardInterrupt) as e:  # not SystemExit: those messages already say what happened
            why = "Ctrl-C" if isinstance(e, KeyboardInterrupt) else str(e) or type(e).__name__
            sys.exit(f"Install stopped part-way ({why}). Everything it changed is recorded in "
                     f"{os.path.relpath(path, ROOT)}: run `tools/canvas restore` to undo it.")
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
    if re.fullmatch(r"\d+( \d+)*", sh(dev, f"pidof {PKG}")):  # process ids; a TV without pidof says "not found"
        problems.append("Canvas is still running, so its phone page is still on the network")
    return problems


def cmd_restore(args):
    with tv() as dev:
        path = backup_path(dev)
        if not os.path.exists(path):
            sys.exit("No backup for this TV in tools/backups/.")
        with open(path) as f:
            backup = json.load(f)
        if "--uninstall" not in args:
            # Canvas puts itself back as the screensaver at boot and on its home screen while it can write the
            # settings, and its phone page runs while it runs. Take the permissions back and stop it first.
            take_permissions_back(dev)
            run(dev, f"am force-stop {PKG}")  # stopped: no BOOT_COMPLETED until something launches it again
        # Also re-enable the two home apps older installs disabled by name, in case one stopped before it recorded
        # them, but not one that was already off before Canvas came. Backups from before `disabled_before` existed
        # do not say, so for them both are re-enabled as before.
        before = backup.get("disabled_before", [])
        packages = list(dict.fromkeys(backup["disabled_by_canvas"]
                                      + [p for p in LEGACY_STOCK_HOMES if installed(dev, p) and p not in before]))
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
        else:
            # Android relaunches the home app it is asked to stop, so the stop above did not hold while Canvas was
            # still the home screen. The TV's own home is back now: stop Canvas again.
            run(dev, f"am force-stop {PKG}")
        problems = check_restored(dev, backup, packages)
        if problems:
            sys.exit("Restore did not take effect on the TV:\n  - " + "\n  - ".join(problems)
                     + "\nThe backup is still in tools/backups/. Run restore again, or fix these on the TV.")
        run(dev, "input keyevent KEYCODE_HOME")
        print("Restored the TV's previous home screen and screensaver.")


OTHER = "Other"
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
DEBLOAT_USAGE = 'usage: tools/canvas debloat|undebloat <list> [--yes | --group "<name>"]'


def parse_groups(text, source="the list"):
    """A device list as [(group name, [(package, description)])], in file order.

    `## Name` starts a group. Every other non-blank line is `package  # description`; a line that starts
    with a single `#` is a comment. Packages above the first `## ` header belong to the group "Other".
    A name that appears twice is one group. Groups with no packages are dropped."""
    groups = {}  # lower-cased name -> (name, {package: description})
    current = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("##"):
            current = line.lstrip("#").strip()
            if not current:
                sys.exit(f"{source} line {number}: a `##` group header needs a name")
            groups.setdefault(current.lower(), (current, {}))
            continue
        code, _, description = line.partition("#")
        pkg = code.strip()
        if not pkg:
            continue
        if not PACKAGE_NAME.fullmatch(pkg):
            sys.exit(f"{source} line {number}: {pkg!r} is not a package name (one per line, then an optional # note)")
        _, packages = groups.setdefault((current or OTHER).lower(), (current or OTHER, {}))
        packages.setdefault(pkg, description.strip())
    return [(name, list(packages.items())) for name, packages in groups.values() if packages]


def read_groups(path):
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        sys.exit(f"Can't read {path}: {e.strerror}")
    groups = parse_groups(text, os.path.basename(path))
    if not groups:
        sys.exit(f"{path} lists no packages.")
    return groups


def find_group(groups, name):
    for group in groups:
        if group[0].lower() == name.strip().lower():
            return group
    sys.exit(f"No group named {name!r}. The groups in this list are: " + ", ".join(repr(g[0]) for g in groups))


def parse_debloat_args(args):
    """(list path, apply everything?, the one group to apply or None). Rejects anything else."""
    path, everything, only = None, False, None
    rest = iter(args)
    for arg in rest:
        if arg == "--yes":
            everything = True
        elif arg == "--group":
            only = next(rest, None)
            if only is None or only.startswith("-"):
                sys.exit('--group needs a group name, for example --group "Sony promos"\n' + DEBLOAT_USAGE)
        elif arg.startswith("-"):
            sys.exit(f"Unknown option {arg}\n" + DEBLOAT_USAGE)
        elif path is None:
            path = arg
        else:
            sys.exit(f"Unexpected argument {arg!r}\n" + DEBLOAT_USAGE)
    if path is None:
        sys.exit(DEBLOAT_USAGE)
    if everything and only is not None:
        sys.exit("Use --yes or --group, not both.\n" + DEBLOAT_USAGE)
    return path, everything, only


def show_group(name, packages):
    print(f"\n{name}")
    width = max(len(pkg) for pkg, _ in packages)
    for pkg, description in packages:
        print(f"  {pkg:<{width}}  {description}".rstrip())


def choose_groups(groups, everything, only, question, ask):
    """The groups to act on. Without --yes or --group, show each group and ask; anything but y/yes is No.
    End of input (Ctrl-D) or Ctrl-C at any question cancels the whole run: nothing is applied."""
    if everything:
        return groups
    if only is not None:
        return [find_group(groups, only)]
    chosen = []
    for group in groups:
        show_group(*group)
        try:
            answer = ask(f"{question} this group? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nCancelled. Nothing was changed on the TV. "
                     "To apply without questions, use --yes or --group \"<name>\".")
        if answer.strip().lower() in ("y", "yes"):
            chosen.append(group)
    return chosen


def first_line(output):
    """What pm said, in one line for the summary: its first line, plus the next one when that first line ends in
    a colon (an exception is reported as `Exception occurred while executing 'x':` then the exception itself)."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "no output from pm"
    return f"{lines[0]} {lines[1]}" if lines[0].endswith(":") and len(lines) > 1 else lines[0]


def cmd_debloat(args, enable=False, ask=None):
    ask = ask or input
    path, everything, only = parse_debloat_args(args)
    groups = read_groups(path)
    chosen = choose_groups(groups, everything, only, "Re-enable" if enable else "Disable", ask)
    if not chosen:
        print("\nNothing selected. The TV was not touched.")
        return
    done = "new state: enabled" if enable else "new state: disabled-user"  # what pm prints when it worked
    problems = {}  # group name -> [(package, what pm said)]
    with tv() as dev:
        for name, packages in chosen:
            print(f"\n{name}")
            for pkg, _ in packages:
                out = run(dev, f"pm enable {pkg}" if enable else f"pm disable-user --user 0 {pkg}")
                if done not in out:
                    problems.setdefault(name, []).append((pkg, first_line(out)))
    verb = "Re-enabled" if enable else "Disabled"
    summary = []
    if any(name not in problems for name, _ in chosen):
        summary.append(f"{verb}: " + ", ".join(name for name, _ in chosen if name not in problems))
    if problems:
        summary.append(f"{verb} with problems: " + ", ".join(
            f"{name} (" + "; ".join(f"{pkg}: {why}" for pkg, why in failed) + ")" for name, failed in problems.items()))
    if len(chosen) < len(groups):
        summary.append("Left alone: " + ", ".join(name for name, _ in groups if name not in [n for n, _ in chosen]))
    print("\n" + ". ".join(summary) + ".")
    if problems:
        sys.exit(1)


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
    elif command in ("debloat", "undebloat") and len(args) > 1:
        cmd_debloat(args[1:], enable=command == "undebloat")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
