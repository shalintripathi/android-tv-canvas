"""Tests for tools/canvas.py that need no TV.

Run from the repo root with the tools venv:

    tools/venv/bin/python -I -m unittest discover -s tools -p 'test_canvas.py' -v

A FakeTV stands in for the device: it answers the shell commands canvas.py sends, keeps its own state
(home screen, enabled packages, settings) and records every command. The relay tests use loopback only.
"""
import errno
import importlib.metadata
import io
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)  # `python -I` leaves the script's own directory off sys.path

import canvas  # noqa: E402

LAUNCHERX, WRAITH = canvas.STOCK_HOMES
STOCK_HOME = LAUNCHERX + "/.home.HomeActivity"
WRAITH_HOME = WRAITH + "/.RecoveryActivity"
FALLBACK_HOME = "com.android.tv.settings/.system.FallbackHome"
VENDOR_HOME = "com.vendor.launcher/.Home"
STOCK_DREAM = "com.google.android.apps.tv.dreamx/.service.Backdrop"
RESOLVER = "android/com.android.internal.app.ResolverActivity"


def exit_status(exc):
    """What the shell sees: sys.exit("message") exits with status 1."""
    return 1 if isinstance(exc.code, str) else exc.code


class FakeTV:
    """Answers canvas.py's shell commands from a small model of the TV and records them all."""

    def __init__(self):
        self.android_id = "abc123"
        self.home = STOCK_HOME
        self.packages = {LAUNCHERX: True, WRAITH: True}  # installed package -> enabled
        self.settings = {("secure", "screensaver_components"): STOCK_DREAM,
                         ("secure", "screensaver_enabled"): "1",
                         ("secure", "screensaver_activate_on_sleep"): "1",
                         ("secure", "sleep_timeout"): "900000",
                         ("system", "screen_off_timeout"): "600000"}
        self.commands = []
        self.closed = False
        self.canvas_can_be_home = True  # False: the build has no HOME activity, so the TV does not list it
        self.stock_outranks = True  # Google TV: the stock launcher wins home resolution over a preferred Canvas
        self.vendor_launcher = False  # True: another launcher install does not disable also outranks Canvas
        self.preferred = STOCK_HOME  # what `cmd package set-home-activity` last set
        self.grant_output = ""
        self.fail_on = None  # raise when a command contains this text (the ADB link dropped)
        self.ignore = ()  # commands starting with one of these do nothing, as if the TV refused them
        self.on_command = None  # called with each command before it is answered

    def close(self):
        self.closed = True

    def shell(self, command, read_timeout_s=None):
        self.commands.append(command)
        if self.on_command:
            self.on_command(command)
        if self.fail_on and self.fail_on in command:
            raise RuntimeError("ADB connection dropped")
        return self._answer(command) + "\n"

    def issued(self, prefix):
        return [c for c in self.commands if c.startswith(prefix)]

    def _home_candidates(self):
        """The HOME activities of installed, enabled packages with their priorities, highest first."""
        everything = [(5, VENDOR_HOME), (2, STOCK_HOME), (1, WRAITH_HOME), (0, canvas.HOME), (-1000, FALLBACK_HOME)]
        found = []
        for priority, component in everything:
            pkg = component.split("/")[0]
            if component == canvas.HOME and not self.canvas_can_be_home:
                continue
            if component == VENDOR_HOME and not self.vendor_launcher:
                continue
            if pkg in canvas.STOCK_HOMES and not self.packages.get(pkg):
                continue  # not installed, or disabled
            found.append((priority, component))
        return found

    def _resolve_home(self):
        """What resolve-activity answers. A preferred home wins only if nothing outranks it; Google TV's does."""
        components = [component for _, component in self._home_candidates()]
        top = components[0]
        if self.preferred in components and (top == canvas.HOME or not self.stock_outranks):
            self.home = self.preferred
        else:
            self.home = top

    def _answer(self, command):
        if self.ignore and command.startswith(self.ignore):
            return ""
        words = command.split()
        if command == "settings get secure android_id":
            return self.android_id
        if command == "getprop ro.product.model":
            return "BRAVIA BF1"
        if command.startswith("cmd package resolve-activity"):
            return "priority=0 preferredOrder=0\n" + self.home if self.home else ""
        if command.startswith("cmd package query-activities"):
            found = self._home_candidates()
            lines = [f"{len(found)} activities found:"]
            for i, (priority, component) in enumerate(found):
                lines += [f"  Activity #{i}:", f"    priority={priority} preferredOrder=0 match=0x108000 isDefault=true",
                          f"    {component}"]
            return "\n".join(lines)
        if command.startswith("settings get"):
            return self.settings.get((words[2], words[3]), "null")
        if command.startswith("pm list packages -d"):
            pkg = words[-1]
            return f"package:{pkg}" if self.packages.get(pkg) is False else ""
        if command.startswith("pm list packages"):
            pkg = words[-1]
            return f"package:{pkg}" if pkg in self.packages else ""
        if command.startswith("pm install"):
            return "Success"
        if command.startswith(("rm ", "am force-stop", "input keyevent", "appops set")):
            return ""
        if command.startswith("pm grant"):
            return self.grant_output
        if command.startswith("cmd package set-home-activity"):
            component = words[-1]
            package = component.split("/")[0]
            if component == canvas.HOME and not self.canvas_can_be_home:
                return "Error: no such activity"
            if self.packages.get(package) is False:
                return "Error: the package is disabled"
            self.preferred = component
            self._resolve_home()
            return "Success"
        if command.startswith("pm disable-user"):
            self.packages[words[-1]] = False
            self._resolve_home()
            return f"Package {words[-1]} new state: disabled-user"
        if command.startswith("pm enable"):
            self.packages[words[-1]] = True
            self._resolve_home()
            return f"Package {words[-1]} new state: enabled"
        if command.startswith("settings put"):
            self.settings[(words[2], words[3])] = words[4]
            return ""
        if command.startswith("settings delete"):
            self.settings.pop((words[2], words[3]), None)
            return "Deleted 1 row"
        if command.startswith("pm uninstall"):
            return "Success"
        raise AssertionError(f"FakeTV was sent a command it does not know: {command!r}")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class TvTestCase(unittest.TestCase):
    """canvas.py wired to a FakeTV, with its backups, screenshots and output redirected."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tv = FakeTV()
        self.stdout = io.StringIO()
        self.patch(canvas, "BACKUPS", os.path.join(tmp.name, "backups"))
        self.patch(canvas, "SHOTS", os.path.join(tmp.name, "shots"))
        self.patch(canvas, "connect", lambda: self.tv)
        self.patch(canvas, "push", lambda dev, local, remote: None)
        self.patch(canvas, "cmd_shot", lambda path: None)
        self.patch(canvas.time, "sleep", lambda seconds: None)
        self.patch(sys, "stdout", self.stdout)
        self.backup_file = os.path.join(canvas.BACKUPS, "abc123.json")

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def backup(self):
        with open(self.backup_file) as f:
            return json.load(f)

    def install(self, *flags):
        canvas.cmd_install(["canvas.apk", *flags])

    def stock_backup(self, **changes):
        data = {"taken": "2026-11-01T00:00:00", "model": "BRAVIA BF1", "home": STOCK_HOME,
                "secure": {"screensaver_components": STOCK_DREAM, "screensaver_enabled": "1",
                           "screensaver_activate_on_sleep": "1", "sleep_timeout": "900000"},
                "system": {"screen_off_timeout": "600000"}, "disabled_by_canvas": []}
        data.update(changes)
        canvas.save_backup(self.backup_file, data)


class BackupBeforeDisableTests(TvTestCase):
    """Finding 1: the backup names a stock package before it is disabled."""

    def test_each_stock_home_is_in_the_backup_file_before_pm_disable_runs(self):
        seen = []

        def look(command):
            if command.startswith("pm disable-user"):
                seen.append((command.split()[-1], self.backup()["disabled_by_canvas"]))

        self.tv.on_command = look
        self.install()
        self.assertEqual(seen, [(LAUNCHERX, [LAUNCHERX]), (WRAITH, [LAUNCHERX, WRAITH])])
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])
        self.assertFalse(os.path.exists(self.backup_file + ".tmp"))

    def test_a_failure_part_way_leaves_a_backup_that_restore_can_use(self):
        self.tv.fail_on = f"pm disable-user --user 0 {WRAITH}"
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])
        self.assertFalse(self.tv.packages[LAUNCHERX])  # the TV really was left without its stock launcher

        self.tv.fail_on = None
        canvas.cmd_restore([])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.tv.home, STOCK_HOME)

    def test_restore_enables_installed_stock_homes_the_backup_does_not_list(self):
        self.stock_backup(disabled_by_canvas=[])
        self.tv.packages[LAUNCHERX] = False  # an install stopped after the disable but before recording it
        canvas.cmd_restore([])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {LAUNCHERX}", f"pm enable {WRAITH}"])
        self.assertTrue(self.tv.packages[LAUNCHERX])

    def test_restore_skips_stock_homes_that_are_not_installed(self):
        del self.tv.packages[WRAITH]
        self.stock_backup(disabled_by_canvas=[])
        canvas.cmd_restore([])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {LAUNCHERX}"])


class InstallSafetyTests(TvTestCase):
    """Canvas must be a HOME candidate before anything is disabled, and must really win once the stock ones are."""

    def test_install_aborts_before_any_disable_when_the_tv_does_not_list_canvas_as_a_home(self):
        self.tv.canvas_can_be_home = False
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("does not list Canvas as a home screen", raised.exception.code)
        self.assertEqual(len(self.tv.issued("cmd package query-activities")), 1)
        for forbidden in ("pm disable-user", "pm enable", "cmd package set-home-activity", "settings put",
                          "input keyevent"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.tv.home, STOCK_HOME)
        self.assertEqual(self.backup()["disabled_by_canvas"], [])

    def test_stock_launcher_outranks_canvas_until_it_is_disabled(self):
        """The FakeTV behaves like the real TV: this is why the old check-before-disabling could never pass."""
        self.tv.shell("cmd package set-home-activity --user 0 " + canvas.HOME)
        self.assertEqual(self.tv.home, STOCK_HOME)
        self.tv.shell(f"pm disable-user --user 0 {LAUNCHERX}")
        self.assertEqual(self.tv.home, WRAITH_HOME)
        self.tv.shell(f"pm disable-user --user 0 {WRAITH}")
        self.assertEqual(self.tv.home, canvas.HOME)

    def test_a_good_full_install_disables_both_stock_homes_verifies_and_only_then_sets_the_screensaver(self):
        self.install()
        commands = self.tv.commands

        def first(prefix):
            return min(i for i, c in enumerate(commands) if c.startswith(prefix))

        def last(prefix):
            return max(i for i, c in enumerate(commands) if c.startswith(prefix))

        self.assertEqual(self.tv.issued("pm disable-user"),
                         [f"pm disable-user --user 0 {LAUNCHERX}", f"pm disable-user --user 0 {WRAITH}"])
        self.assertEqual(len(self.tv.issued("cmd package set-home-activity")), 2)
        self.assertLess(first("cmd package query-activities"), first("cmd package set-home-activity"))
        self.assertLess(first("cmd package set-home-activity"), first("pm disable-user"))
        self.assertLess(last("pm disable-user"), last("cmd package set-home-activity"))
        verify = last("cmd package resolve-activity")
        self.assertLess(last("cmd package set-home-activity"), verify)
        self.assertLess(verify, first("settings put"))
        self.assertLess(first("settings put"), first("input keyevent"))
        self.assertEqual(self.tv.issued("pm enable"), [])
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])

    def test_a_home_mismatch_after_disabling_re_enables_exactly_the_packages_disabled_this_run(self):
        self.tv.vendor_launcher = True  # something else outranks Canvas, so disabling the stock ones is not enough
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("did not become the home screen", raised.exception.code)
        self.assertIn(VENDOR_HOME, raised.exception.code)
        self.assertIn("Re-enabled the stock launcher", raised.exception.code)
        self.assertEqual(self.tv.issued("pm disable-user"),
                         [f"pm disable-user --user 0 {LAUNCHERX}", f"pm disable-user --user 0 {WRAITH}"])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {LAUNCHERX}", f"pm enable {WRAITH}"])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.backup()["disabled_by_canvas"], [])
        for forbidden in ("settings put", "am force-stop", "input keyevent"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], STOCK_DREAM)

    def test_a_rollback_leaves_packages_an_earlier_run_disabled_alone(self):
        self.tv.packages[WRAITH] = False
        self.stock_backup(disabled_by_canvas=[WRAITH])
        self.tv.vendor_launcher = True
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.tv.issued("pm disable-user"), [f"pm disable-user --user 0 {LAUNCHERX}"])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {LAUNCHERX}"])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: False})
        self.assertEqual(self.backup()["disabled_by_canvas"], [WRAITH])

    def test_a_rollback_that_cannot_re_enable_keeps_the_package_in_the_backup_and_says_so(self):
        self.tv.vendor_launcher = True
        self.tv.ignore = ("pm enable",)
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("could not be re-enabled", raised.exception.code)
        self.assertIn("tools/canvas restore", raised.exception.code)
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])

    def test_full_install_stops_before_touching_the_home_when_the_grant_fails(self):
        self.tv.grant_output = ("Exception occurred while executing 'grant':\n"
                                "java.lang.SecurityException: Package has not requested permission")
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("WRITE_SECURE_SETTINGS", raised.exception.code)
        for forbidden in ("cmd package set-home-activity", "pm disable-user", "settings put"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)
        self.assertEqual(self.tv.home, STOCK_HOME)

    def test_app_only_install_prints_the_grant_failure_and_carries_on(self):
        self.tv.grant_output = "Exception occurred while executing 'grant':"
        self.install("--app-only")
        self.assertIn("Installed (app only).", self.stdout.getvalue())
        self.assertEqual(len(self.tv.issued("appops set")), 1)
        for forbidden in ("cmd package set-home-activity", "pm disable-user", "settings put"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)

    def test_a_good_full_install_makes_canvas_home_and_disables_the_stock_homes(self):
        self.install()
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.tv.packages, {LAUNCHERX: False, WRAITH: False})
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], canvas.DREAM)


class RestoreVerificationTests(TvTestCase):
    """Finding 3: restore reads the TV back and fails loudly when it did not take."""

    def setUp(self):
        super().setUp()
        self.install()  # a Canvas TV with a stock backup to go back to
        self.tv.commands.clear()

    def test_restore_puts_everything_back_and_returns_normally(self):
        canvas.cmd_restore([])
        self.assertEqual(self.tv.home, STOCK_HOME)
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], STOCK_DREAM)
        self.assertEqual(self.tv.settings[("secure", "sleep_timeout")], "900000")
        self.assertEqual(self.tv.settings[("system", "screen_off_timeout")], "600000")
        self.assertIn("Restored", self.stdout.getvalue())
        self.assertEqual(len(self.tv.issued("input keyevent")), 1)

    def test_restore_exits_non_zero_when_the_home_screen_does_not_come_back(self):
        self.tv.stock_outranks = False  # a TV where re-enabling the stock launcher alone would not take the home back
        self.tv.ignore = ("cmd package set-home-activity",)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_restore([])
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("the home screen is " + canvas.HOME, raised.exception.code)
        self.assertEqual(self.tv.issued("input keyevent"), [])
        self.assertNotIn("Restored", self.stdout.getvalue())

    def test_restore_exits_non_zero_when_a_stock_package_stays_disabled(self):
        self.tv.ignore = ("pm enable",)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_restore([])
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn(f"{LAUNCHERX} is still disabled", raised.exception.code)
        self.assertIn(f"{WRAITH} is still disabled", raised.exception.code)

    def test_restore_exits_non_zero_when_a_setting_does_not_read_back_as_backed_up(self):
        self.tv.ignore = ("settings put secure screensaver_components",)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_restore([])
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("secure screensaver_components", raised.exception.code)
        self.assertIn(STOCK_DREAM, raised.exception.code)

    def test_restore_exits_non_zero_when_a_setting_that_was_unset_is_still_set(self):
        data = self.backup()
        data["secure"]["sleep_timeout"] = "null"
        canvas.save_backup(self.backup_file, data)
        self.tv.ignore = ("settings delete",)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_restore([])
        self.assertIn("secure sleep_timeout", raised.exception.code)

    def test_restore_with_uninstall_removes_the_app_and_still_succeeds(self):
        canvas.cmd_restore(["--uninstall"])
        self.assertEqual(self.tv.issued("pm uninstall"), [f"pm uninstall {canvas.PKG}"])

    def test_restore_without_a_backup_exits_non_zero(self):
        os.remove(self.backup_file)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_restore([])
        self.assertNotEqual(exit_status(raised.exception), 0)


class BackupRefusalTests(TvTestCase):
    """Finding 4: a backup of an already-modified TV would make `restore` keep Canvas."""

    def assertRefused(self, why):
        with self.assertRaises(SystemExit) as raised:
            canvas.take_backup(self.tv)
        self.assertNotEqual(exit_status(raised.exception), 0, why)
        self.assertIn("Refusing to back up", raised.exception.code, why)
        self.assertFalse(os.path.exists(self.backup_file), why)
        self.assertTrue(all(c.startswith(("settings get", "cmd package resolve-activity")) for c in self.tv.commands),
                        f"{why}: only reads are allowed, got {self.tv.commands}")

    def test_refuses_when_canvas_is_already_the_home(self):
        self.tv.home = canvas.HOME
        self.assertRefused("Canvas home")

    def test_refuses_when_canvas_is_already_the_screensaver(self):
        self.tv.settings[("secure", "screensaver_components")] = canvas.DREAM
        self.assertRefused("Canvas screensaver")

    def test_refuses_an_empty_home(self):
        self.tv.home = ""
        self.assertRefused("empty home")

    def test_refuses_a_resolver_home(self):
        self.tv.home = RESOLVER
        self.assertRefused("resolver home")

    def test_install_on_a_canvas_tv_with_no_backup_changes_nothing(self):
        self.tv.home = canvas.HOME
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.tv.issued("pm install"), [])
        self.assertEqual(self.tv.issued("pm disable-user"), [])

    def test_takes_a_backup_of_a_stock_tv(self):
        path = canvas.take_backup(self.tv)
        self.assertEqual(path, self.backup_file)
        data = self.backup()
        self.assertEqual(data["home"], STOCK_HOME)
        self.assertEqual(data["secure"]["screensaver_components"], STOCK_DREAM)
        self.assertEqual(data["disabled_by_canvas"], [])

    def test_an_existing_backup_is_reused_unchanged_even_on_a_canvas_tv(self):
        self.stock_backup()
        before = read_bytes(self.backup_file)
        mtime = os.stat(self.backup_file).st_mtime_ns
        self.tv.home = canvas.HOME
        self.tv.settings[("secure", "screensaver_components")] = canvas.DREAM
        self.assertEqual(canvas.take_backup(self.tv), self.backup_file)
        self.assertEqual(read_bytes(self.backup_file), before)
        self.assertEqual(os.stat(self.backup_file).st_mtime_ns, mtime)
        self.install("--app-only")  # re-installing the app on a Canvas TV is still fine
        self.assertEqual(read_bytes(self.backup_file), before)


class SameComponentTests(unittest.TestCase):
    def test_short_and_full_class_names_match(self):
        self.assertTrue(canvas.same_component(canvas.HOME, canvas.PKG + "/" + canvas.PKG + ".home.HomeActivity"))
        self.assertTrue(canvas.same_component(STOCK_HOME, STOCK_HOME))
        self.assertFalse(canvas.same_component(STOCK_HOME, canvas.HOME))
        self.assertFalse(canvas.same_component("", canvas.HOME))
        self.assertTrue(canvas.same_component("", ""))


class RequirementsTests(unittest.TestCase):
    """Finding 6: the bulk_write patch was validated against one adb-shell version."""

    def test_adb_shell_is_pinned_to_the_version_the_patch_was_checked_against(self):
        with open(os.path.join(TOOLS, "requirements.txt")) as f:
            lines = [line.strip() for line in f]
        self.assertIn("adb-shell==0.4.4", lines)
        self.assertEqual(importlib.metadata.version("adb-shell"), "0.4.4")


@unittest.skipUnless(platform.system() == "Darwin" and os.path.exists("/usr/bin/nc"), "the nc relay is macOS only")
class RelayTests(unittest.TestCase):
    """Finding 5: loopback only. The fake TV accepts connections and never closes them, as a real adbd would."""

    def setUp(self):
        self.tv_server = socket.socket()
        self.tv_server.bind(("127.0.0.1", 0))
        self.tv_server.listen(8)
        self.tv_port = self.tv_server.getsockname()[1]
        self.tv_conns = []
        threading.Thread(target=self._tv_accept, daemon=True).start()
        self.addCleanup(self._tv_stop)

    def _tv_accept(self):
        while True:
            try:
                conn, _ = self.tv_server.accept()
            except OSError:
                return
            self.tv_conns.append(conn)

    def _tv_stop(self):
        self.tv_server.close()
        for conn in self.tv_conns:
            conn.close()

    def nc_children(self):
        ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
        return [line for line in ps.splitlines() if f"/usr/bin/nc 127.0.0.1 {self.tv_port}" in line]

    def wait_until(self, condition, what, seconds=5):
        end = time.time() + seconds
        while time.time() < end:
            if condition():
                return
            time.sleep(0.02)
        self.fail(f"timed out waiting for {what}")

    def open_relay(self):
        relay = canvas.NcRelay("127.0.0.1", self.tv_port)
        self.addCleanup(relay.close)
        client = socket.create_connection(("127.0.0.1", relay.local_port))
        self.addCleanup(client.close)
        self.wait_until(lambda: len(self.tv_conns) == len(relay._conns) >= 1, "nc to reach the fake TV")
        return relay, client

    def test_the_relay_still_carries_bytes_both_ways(self):
        relay, client = self.open_relay()
        tv_side = self.tv_conns[0]
        tv_side.settimeout(5)
        client.sendall(b"CNXN")
        self.assertEqual(tv_side.recv(4), b"CNXN")
        tv_side.sendall(b"OKAY")
        client.settimeout(5)
        self.assertEqual(client.recv(4), b"OKAY")

    def test_close_kills_and_reaps_the_nc_child_while_the_tv_keeps_the_socket_open(self):
        relay, client = self.open_relay()
        procs = [proc for _, proc, _ in relay._conns]
        self.assertEqual(len(self.nc_children()), 1)
        self.assertIsNone(procs[0].poll())

        relay.close()

        self.assertIsNotNone(procs[0].returncode, "nc was killed and waited for")
        self.assertEqual(self.nc_children(), [])
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", relay.local_port), timeout=2)
        relay.close()  # closing twice is harmless

    def test_close_reaps_every_connection(self):
        relay, client = self.open_relay()
        second = socket.create_connection(("127.0.0.1", relay.local_port))
        self.addCleanup(second.close)
        self.wait_until(lambda: len(self.nc_children()) == 2, "two nc children")
        relay.close()
        self.assertEqual(self.nc_children(), [])

    def test_nc_child_is_reaped_as_soon_as_its_client_closes(self):
        relay, client = self.open_relay()
        proc = relay._conns[0][1]
        client.close()
        self.wait_until(lambda: proc.returncode is not None, "nc to be killed after the client closed")
        self.assertEqual(self.nc_children(), [])

    def fake_connect(self, host, port):
        """Stand in for _connect: the direct attempt fails as macOS Local Network privacy does; the relay one works."""
        if port == self.tv_port:
            raise OSError(errno.EHOSTUNREACH, "No route to host")
        sock = socket.create_connection((host, port))
        dev = mock.Mock()
        dev.close.side_effect = sock.close
        return dev

    def test_tv_context_closes_the_relay_and_the_device_on_success(self):
        env = {"TV_HOST": "127.0.0.1", "TV_PORT": str(self.tv_port)}
        with mock.patch.object(canvas, "_connect", self.fake_connect), mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            with canvas.tv() as dev:
                self.wait_until(lambda: len(self.nc_children()) == 1, "the relay's nc child")
            dev.close.assert_called_once()
        self.assertEqual(self.nc_children(), [])

    def test_tv_context_closes_the_relay_when_the_command_exits_early(self):
        env = {"TV_HOST": "127.0.0.1", "TV_PORT": str(self.tv_port)}
        with mock.patch.object(canvas, "_connect", self.fake_connect), mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                with canvas.tv():
                    self.wait_until(lambda: len(self.nc_children()) == 1, "the relay's nc child")
                    sys.exit("Install failed.")
        self.assertEqual(self.nc_children(), [])

    def test_connect_closes_the_relay_when_the_adb_handshake_through_it_fails(self):
        def refuse(host, port):
            if port == self.tv_port:
                raise OSError(errno.EHOSTUNREACH, "No route to host")
            socket.create_connection((host, port)).close()
            raise RuntimeError("handshake failed")

        env = {"TV_HOST": "127.0.0.1", "TV_PORT": str(self.tv_port)}
        with mock.patch.object(canvas, "_connect", refuse), mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(RuntimeError):
                canvas.connect()
        self.wait_until(lambda: self.nc_children() == [], "no nc child to survive a failed handshake")

    COMMAND = textwrap.dedent('''
        import errno, os, socket, subprocess, sys, time
        sys.path.insert(0, sys.argv[1])
        import canvas

        tv_port = int(os.environ["TV_PORT"])
        marker = "/usr/bin/nc 127.0.0.1 %d" % tv_port

        def nc_children():
            ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
            return sum(marker in line for line in ps.splitlines())

        class Dev:
            def __init__(self, sock):
                self.sock = sock
                self.reported = False
            def shell(self, command, read_timeout_s=None):
                if not self.reported:
                    self.reported = True
                    end = time.time() + 5
                    while nc_children() == 0 and time.time() < end:
                        time.sleep(0.02)
                    print("NC_CHILDREN_DURING_COMMAND=%d" % nc_children(), flush=True)
                return ""
            def close(self):
                self.sock.close()

        def fake_connect(host, port):
            if port == tv_port:
                raise OSError(errno.EHOSTUNREACH, "No route to host")
            return Dev(socket.create_connection((host, port)))

        canvas._connect = fake_connect
        canvas.cmd_probe()
    ''')

    def test_a_whole_command_leaves_no_nc_child_behind_after_the_process_ends(self):
        with tempfile.TemporaryDirectory() as scratch:
            script = os.path.join(scratch, "run_probe.py")
            with open(script, "w") as f:
                f.write(self.COMMAND)
            env = dict(os.environ, TV_HOST="127.0.0.1", TV_PORT=str(self.tv_port))
            done = subprocess.run([sys.executable, "-I", script, TOOLS], env=env, capture_output=True, text=True,
                                  timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("NC_CHILDREN_DURING_COMMAND=1", done.stdout)  # the relay really was in use
        self.wait_until(lambda: self.nc_children() == [], "the command's nc child to be gone", seconds=3)


if __name__ == "__main__":
    unittest.main()
