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

# The Google TV on the test TV: its launcher, a setup screen that also declares a HOME activity, and the
# Settings app's FallbackHome, the system's safety net.
LAUNCHERX = "com.google.android.apps.tv.launcherx"
WRAITH = "com.google.android.tungsten.setupwraith"
STOCK_HOME = LAUNCHERX + "/.home.HomeActivity"
WRAITH_HOME = WRAITH + "/.RecoveryActivity"
FALLBACK_HOME = "com.android.tv.settings/.system.FallbackHome"
SETTINGS_HOME = "com.android.settings/.FallbackHome"
# Other TVs: the Android TV (not Google TV) launcher, a vendor launcher, and one made up for the "stubborn" tests.
TVLAUNCHER = "com.google.android.tvlauncher"
TVLAUNCHER_HOME = TVLAUNCHER + "/.MainActivity"
MITV = "com.mitv.tvhome.atv"
MITV_HOME = MITV + "/.MainActivity"
VENDOR = "com.vendor.launcher"
VENDOR_HOME = VENDOR + "/.Home"
VENDOR_SETTINGS = "com.vendor.tv.settings"  # a vendor's own Settings app that hosts its own FallbackHome
VENDOR_SETTINGS_HOME = VENDOR_SETTINGS + "/.FallbackHome"
SYSTEM_PACKAGES = (canvas.PKG, "com.android.tv.settings", "com.android.settings")  # always installed and enabled
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
        self.homes = {STOCK_HOME: 2, WRAITH_HOME: 1, canvas.HOME: 0, FALLBACK_HOME: -1000}  # HOME activity -> priority
        self.preferred = STOCK_HOME  # what `cmd package set-home-activity` last set
        self.grant_output = ""
        self.install_output = "Success"  # what `pm install` prints
        self.fail_on = None  # raise when a command contains this text (the ADB link dropped)
        self.ignore = ()  # commands starting with one of these do nothing, as if the TV refused them
        self.on_command = None  # called with each command before it is answered
        self.pm_errors = {}  # package -> what pm prints instead of changing it (see UNKNOWN_PACKAGE)
        self.grant_errors = {}  # permission -> what `pm grant` prints instead of granting it
        self.granted = set()  # Canvas's permissions from `pm grant` and `appops set` (as WRITE_SETTINGS)
        self.running = set()  # packages with a live process

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

    def add_home(self, component, priority):
        """Another launcher on the TV: installed, enabled, and listed as a HOME activity."""
        pkg = component.split("/")[0]
        if pkg not in SYSTEM_PACKAGES:
            self.packages[pkg] = True
        self.homes[component] = priority
        self.preferred = self.home = self._home_candidates()[0][1]  # the TV's home is whatever now wins

    def replace_google_tv(self, *homes):
        """A TV that does not have Google TV's launchers, with these (component, priority) HOME activities instead."""
        for component in (STOCK_HOME, WRAITH_HOME):
            del self.homes[component]
        self.packages.clear()
        for component, priority in homes:
            self.add_home(component, priority)

    def _home_candidates(self):
        """The HOME activities of installed, enabled packages with their priorities, highest first."""
        found = []
        for component, priority in sorted(self.homes.items(), key=lambda item: -item[1]):
            pkg = component.split("/")[0]
            if component == canvas.HOME and not self.canvas_can_be_home:
                continue
            if pkg not in SYSTEM_PACKAGES and not self.packages.get(pkg):
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

    def _stop(self, pkg):
        """Kill a package's process. Android starts the home app again at once if that is what was killed."""
        if self.home.split("/")[0] != pkg:
            self.running.discard(pkg)

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
        if command == "pm list packages -d":
            return "\n".join(f"package:{pkg}" for pkg, on in self.packages.items() if on is False)
        if command.startswith("pm list packages -d"):
            pkg = words[-1]
            return f"package:{pkg}" if self.packages.get(pkg) is False else ""
        if command.startswith("pm list packages"):
            pkg = words[-1]
            return f"package:{pkg}" if pkg in self.packages else ""
        if command.startswith("pm install"):
            return self.install_output
        if command.startswith("rm "):
            return ""
        if command == "input keyevent KEYCODE_HOME":
            self.running.add(self.home.split("/")[0])
            return ""
        if command.startswith("am force-stop"):
            self._stop(words[-1])
            return ""
        if command.startswith("pidof"):
            return "4242" if words[-1] in self.running else ""
        if command.startswith("appops set"):
            (self.granted.add if words[-1] == "allow" else self.granted.discard)(words[-2])
            return ""
        if command.startswith("pm grant"):
            out = self.grant_output or self.grant_errors.get(words[-1], "")
            if not out:
                self.granted.add(words[-1])
            return out
        if command.startswith("pm revoke"):
            self.granted.discard(words[-1])
            if words[-1] in canvas.LOCATION:  # taking back a runtime permission kills the app
                self._stop(words[-2])
            return ""
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
        if command.startswith(("pm disable-user", "pm enable")) and words[-1] in self.pm_errors:
            return self.pm_errors[words[-1]]
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
            self.running.discard(words[-1])
            self.granted.clear()
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
        """A full or app-only install with the question answered (--yes), the way the older tests expect."""
        canvas.cmd_install(["canvas.apk", "--yes", *flags])

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
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])
        self.assertFalse(self.tv.packages[LAUNCHERX])  # the TV really was left without its stock launcher

        self.tv.fail_on = None
        canvas.cmd_restore([])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.tv.home, STOCK_HOME)

    def test_a_failure_part_way_says_so_names_the_backup_and_says_to_run_restore(self):
        self.tv.fail_on = f"pm disable-user --user 0 {WRAITH}"
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        message = raised.exception.code
        self.assertTrue(message.startswith("Install stopped part-way (ADB connection dropped). "
                                           "Everything it changed is recorded in "), message)
        self.assertTrue(message.endswith("abc123.json: run `tools/canvas restore` to undo it."), message)
        self.assertEqual(self.tv.issued("settings put"), [])
        self.assertTrue(self.tv.closed)

    def test_ctrl_c_part_way_is_reported_the_same_way(self):
        def interrupt(command):
            if command == f"pm disable-user --user 0 {LAUNCHERX}":
                raise KeyboardInterrupt

        self.tv.on_command = interrupt
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("Install stopped part-way (Ctrl-C)", raised.exception.code)
        self.assertIn("run `tools/canvas restore` to undo it", raised.exception.code)
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX])

    def test_a_failure_before_anything_was_changed_needs_no_restore(self):
        """The pre-check and the question come before the try: those still stop with their own messages."""
        self.tv.canvas_can_be_home = False
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotIn("part-way", raised.exception.code)

    def test_restore_enables_installed_stock_homes_the_backup_does_not_list(self):
        self.stock_backup(disabled_by_canvas=[])
        self.tv.packages[LAUNCHERX] = False  # an install stopped after the disable but before recording it
        canvas.cmd_restore([])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {LAUNCHERX}", f"pm enable {WRAITH}"])
        self.assertTrue(self.tv.packages[LAUNCHERX])

    def test_the_backup_records_what_was_already_disabled_before_canvas(self):
        self.tv.packages["com.sony.dtv.promos"] = False
        canvas.take_backup(self.tv)
        self.assertEqual(self.backup()["disabled_before"], ["com.sony.dtv.promos"])

    def test_a_launcherx_the_owner_had_disabled_before_canvas_stays_disabled_after_restore(self):
        self.tv.add_home(MITV_HOME, 5)  # the owner used the vendor launcher and had switched Google's off
        self.tv.packages[LAUNCHERX] = False
        self.tv.preferred = self.tv.home = MITV_HOME
        self.install()
        self.assertEqual(self.backup()["disabled_before"], [LAUNCHERX])
        self.assertEqual(self.backup()["disabled_by_canvas"], [MITV, WRAITH])
        canvas.cmd_restore([])
        self.assertNotIn(f"pm enable {LAUNCHERX}", self.tv.commands)
        self.assertEqual(self.tv.packages, {LAUNCHERX: False, WRAITH: True, MITV: True})
        self.assertEqual(self.tv.home, MITV_HOME)
        self.assertIn("Restored", self.stdout.getvalue())

    def test_an_old_backup_without_the_disabled_list_still_re_enables_both_legacy_homes(self):
        self.stock_backup(disabled_by_canvas=[])
        self.assertNotIn("disabled_before", self.backup())
        self.tv.packages[LAUNCHERX] = False
        self.tv.packages[WRAITH] = False
        canvas.cmd_restore([])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {LAUNCHERX}", f"pm enable {WRAITH}"])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})

    def test_restore_still_knows_the_two_home_apps_older_installs_disabled_by_name(self):
        self.assertEqual(canvas.LEGACY_STOCK_HOMES, [LAUNCHERX, WRAITH])

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

    def stubborn_vendor_launcher(self):
        """A launcher that outranks Canvas and that this firmware will not let `pm disable-user` switch off."""
        self.tv.add_home(VENDOR_HOME, 5)
        self.tv.ignore = (f"pm disable-user --user 0 {VENDOR}",)

    def test_a_home_mismatch_after_disabling_re_enables_exactly_the_packages_disabled_this_run(self):
        self.stubborn_vendor_launcher()
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("did not become the home screen", raised.exception.code)
        self.assertIn(VENDOR_HOME, raised.exception.code)
        self.assertIn("Re-enabled the TV's own home app", raised.exception.code)
        self.assertIn("took back Canvas's permissions", raised.exception.code)
        self.assertEqual(self.tv.granted, set())
        everything = [VENDOR, LAUNCHERX, WRAITH]  # the TV's order: highest priority first
        self.assertEqual(self.tv.issued("pm disable-user"), [f"pm disable-user --user 0 {p}" for p in everything])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {p}" for p in everything])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True, VENDOR: True})
        self.assertEqual(self.backup()["disabled_by_canvas"], [])
        for forbidden in ("settings put", "am force-stop", "input keyevent"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], STOCK_DREAM)

    def test_a_rollback_leaves_packages_an_earlier_run_disabled_alone(self):
        self.tv.packages[WRAITH] = False
        self.stock_backup(disabled_by_canvas=[WRAITH])
        self.stubborn_vendor_launcher()
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.tv.issued("pm disable-user"),
                         [f"pm disable-user --user 0 {VENDOR}", f"pm disable-user --user 0 {LAUNCHERX}"])
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {VENDOR}", f"pm enable {LAUNCHERX}"])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: False, VENDOR: True})
        self.assertEqual(self.backup()["disabled_by_canvas"], [WRAITH])

    def test_a_rollback_that_cannot_re_enable_keeps_the_package_in_the_backup_and_says_so(self):
        self.stubborn_vendor_launcher()
        self.tv.ignore += ("pm enable",)
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("could not be re-enabled", raised.exception.code)
        self.assertIn("tools/canvas restore", raised.exception.code)
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])  # the vendor one never went off

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

    def test_a_grant_that_fails_after_a_typed_yes_stops_before_the_home_or_any_disable(self):
        self.tv.grant_output = "java.lang.SecurityException: Package has not requested permission"
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_install(["canvas.apk"], ask=Answers("y"))
        self.assertIn("could not be granted WRITE_SECURE_SETTINGS", raised.exception.code)
        self.assertNotIn("part-way", raised.exception.code)
        for forbidden in ("appops set", "cmd package set-home-activity", "pm disable-user", "settings put"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.backup()["disabled_by_canvas"], [])

    def test_the_permissions_are_granted_after_the_question_and_before_the_home_is_touched(self):
        seen = []

        def ask(prompt):
            seen.extend(self.tv.commands)
            return "y"

        canvas.cmd_install(["canvas.apk"], ask=ask)
        self.assertEqual([c for c in seen if c.startswith(("pm grant", "appops set"))], [])
        commands = self.tv.commands
        grant = commands.index(f"pm grant {canvas.PKG} android.permission.WRITE_SECURE_SETTINGS")
        appops = commands.index(f"appops set {canvas.PKG} WRITE_SETTINGS allow")
        first_home = min(i for i, c in enumerate(commands) if c.startswith("cmd package set-home-activity"))
        self.assertLessEqual(len(seen), grant)  # issued after the answer came back
        self.assertLess(grant, appops)
        self.assertLess(appops, first_home)

    def test_a_full_install_grants_location_and_both_settings_permissions_after_the_yes(self):
        canvas.cmd_install(["canvas.apk"], ask=Answers("y"))
        self.assertEqual(self.tv.issued("pm install"), ["pm install -r /data/local/tmp/canvas.apk"])  # no -g
        self.assertEqual(self.tv.granted, {"android.permission.WRITE_SECURE_SETTINGS", "WRITE_SETTINGS",
                                           *canvas.LOCATION})

    def test_a_refused_location_grant_only_costs_the_wifi_name_in_the_header(self):
        self.tv.grant_errors = {permission: "java.lang.SecurityException: not allowed" for permission in canvas.LOCATION}
        canvas.cmd_install(["canvas.apk"], ask=Answers("y"))
        self.assertEqual(self.stdout.getvalue().count("will not show the Wi-Fi network's name"), 1)
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], canvas.DREAM)

    def test_app_only_install_grants_nothing_and_carries_on(self):
        self.tv.grant_output = "Exception occurred while executing 'grant':"  # would fail, but is never asked for
        self.install("--app-only")
        self.assertIn("Installed (app only).", self.stdout.getvalue())
        for forbidden in ("pm grant", "appops set", "cmd package set-home-activity", "pm disable-user",
                          "settings put"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)

    def test_a_build_signed_with_another_key_says_how_to_switch_builds(self):
        self.tv.install_output = ("Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package "
                                  f"{canvas.PKG} signatures do not match newer version; ignoring!]")
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn("Install failed.", raised.exception.code)
        self.assertIn("`tools/canvas restore --uninstall`, then `tools/canvas install <apk>`", raised.exception.code)
        self.assertIn("deletes the photos", raised.exception.code)
        for forbidden in ("pm grant", "pm uninstall", "pm disable-user", "settings put"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)

    def test_a_good_full_install_makes_canvas_home_and_disables_the_stock_homes(self):
        self.install()
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.tv.packages, {LAUNCHERX: False, WRAITH: False})
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], canvas.DREAM)


class StockHomeDetectionTests(unittest.TestCase):
    """Which packages `install` switches off is read from the TV's HOME activities, not from a fixed list."""

    def test_canvas_and_the_system_settings_apps_are_never_switched_off(self):
        components = [STOCK_HOME, WRAITH_HOME, canvas.HOME, FALLBACK_HOME, SETTINGS_HOME]
        self.assertEqual(canvas.stock_homes(components), [LAUNCHERX, WRAITH])

    def test_an_android_tv_tvlauncher_is_found(self):
        self.assertEqual(canvas.stock_homes([TVLAUNCHER_HOME, canvas.HOME, FALLBACK_HOME]), [TVLAUNCHER])

    def test_a_vendor_launcher_is_found(self):
        self.assertEqual(canvas.stock_homes([MITV_HOME, canvas.HOME, FALLBACK_HOME, SETTINGS_HOME]), [MITV])

    def test_a_package_with_two_home_activities_is_listed_once_and_the_tvs_order_is_kept(self):
        components = [MITV_HOME, TVLAUNCHER_HOME, MITV + "/.OtherHome", canvas.HOME]
        self.assertEqual(canvas.stock_homes(components), [MITV, TVLAUNCHER])

    def test_a_tv_with_nothing_but_canvas_and_the_fallback_has_nothing_to_switch_off(self):
        self.assertEqual(canvas.stock_homes([canvas.HOME, FALLBACK_HOME]), [])
        self.assertEqual(canvas.stock_homes([]), [])

    def test_only_the_component_lines_of_the_brief_listing_are_read(self):
        tv = FakeTV()
        tv.add_home(MITV_HOME, 5)
        listing = tv.shell("cmd package query-activities --brief -a android.intent.action.MAIN")
        self.assertIn("activities found:", listing)  # the count line and the priority lines are in the output...
        self.assertIn("priority=", listing)
        self.assertEqual(canvas.home_components(tv),  # ...and none of them is taken for a component
                         [MITV_HOME, STOCK_HOME, WRAITH_HOME, canvas.HOME, FALLBACK_HOME])

    def test_a_vendor_settings_package_with_a_fallback_home_is_kept(self):
        components = [MITV_HOME, VENDOR_SETTINGS_HOME, canvas.HOME, FALLBACK_HOME]
        self.assertEqual(canvas.stock_homes(components), [MITV])

    def test_a_package_with_a_fallback_home_is_kept_whole_even_when_it_has_another_home_activity(self):
        components = [VENDOR_SETTINGS + "/.Home", MITV_HOME, VENDOR_SETTINGS + "/.system.FallbackHome", canvas.HOME]
        self.assertEqual(canvas.stock_homes(components), [MITV])

    def test_only_a_class_named_fallback_home_makes_a_package_a_safety_net(self):
        self.assertEqual(canvas.stock_homes([VENDOR + "/.FallbackHomeScreen", VENDOR + "/.NotAFallbackHome2"]),
                         [VENDOR])

    def test_the_settings_apps_and_canvas_are_what_is_kept(self):
        self.assertEqual(sorted(canvas.KEEP_HOMES), sorted(SYSTEM_PACKAGES))


class GenericInstallTests(TvTestCase):
    """Full installs on TVs that are not the Google TV the tool was first written on."""

    def disabled(self):
        return [c.split()[-1] for c in self.tv.issued("pm disable-user")]

    def test_an_android_tv_with_the_tvlauncher_ends_up_on_canvas_and_restores_cleanly(self):
        self.tv.replace_google_tv((TVLAUNCHER_HOME, 2), (SETTINGS_HOME, -1000), (FALLBACK_HOME, -1000))
        self.install()
        self.assertEqual(self.disabled(), [TVLAUNCHER])
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.backup()["disabled_by_canvas"], [TVLAUNCHER])
        self.assertEqual(self.backup()["home"], TVLAUNCHER_HOME)
        self.assertIn(f"{TVLAUNCHER}\n", self.stdout.getvalue())
        canvas.cmd_restore([])
        self.assertEqual(self.tv.home, TVLAUNCHER_HOME)
        self.assertEqual(self.tv.packages, {TVLAUNCHER: True})
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {TVLAUNCHER}"])  # the old fixed list is not installed here

    def test_a_vendor_launcher_goes_off_with_the_google_ones_but_the_system_fallbacks_stay(self):
        self.tv.add_home(MITV_HOME, 5)
        self.tv.add_home(SETTINGS_HOME, -1000)
        seen = []

        def look(command):
            if command.startswith("pm disable-user"):
                seen.append((command.split()[-1], self.backup()["disabled_by_canvas"][:]))

        self.tv.on_command = look
        self.install()
        self.assertEqual(self.disabled(), [MITV, LAUNCHERX, WRAITH])
        self.assertEqual(seen, [(MITV, [MITV]), (LAUNCHERX, [MITV, LAUNCHERX]), (WRAITH, [MITV, LAUNCHERX, WRAITH])])
        self.assertEqual(self.tv.home, canvas.HOME)
        for kept in (canvas.PKG, "com.android.tv.settings", "com.android.settings"):
            self.assertNotIn(kept, self.disabled())
        canvas.cmd_restore([])
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True, MITV: True})
        self.assertEqual(self.tv.home, MITV_HOME)

    def test_a_vendor_settings_fallback_home_is_never_listed_or_disabled(self):
        self.tv.add_home(MITV_HOME, 5)
        self.tv.add_home(VENDOR_SETTINGS_HOME, -1000)
        self.install()
        self.assertEqual(self.disabled(), [MITV, LAUNCHERX, WRAITH])
        self.assertNotIn(VENDOR_SETTINGS, self.stdout.getvalue())
        self.assertTrue(self.tv.packages[VENDOR_SETTINGS])
        self.assertEqual(self.tv.home, canvas.HOME)

    def test_installing_again_on_a_tv_that_is_already_on_canvas_switches_nothing_else_off(self):
        self.install()
        self.tv.commands.clear()
        self.install()
        self.assertEqual(self.disabled(), [])
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])

    def test_the_pre_check_still_comes_first_and_the_listing_is_asked_for_once(self):
        self.tv.add_home(MITV_HOME, 5)
        self.tv.canvas_can_be_home = False
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn("does not list Canvas as a home screen", raised.exception.code)
        self.assertEqual(len(self.tv.issued("cmd package query-activities")), 1)
        self.assertEqual(self.disabled(), [])
        self.assertTrue(self.tv.packages[MITV])


class InstallConfirmationTests(TvTestCase):
    """A full install lists the TV's own home apps and asks before switching them off; --yes answers for you."""

    QUESTION = "Disable these and make Canvas the home screen? [y/N] "

    def run_install(self, ask, *flags):
        canvas.cmd_install(["canvas.apk", *flags], ask=ask)

    def assert_stopped_with_only_the_app_installed(self, ask, *flags):
        with self.assertRaises(SystemExit) as raised:
            self.run_install(ask, *flags)
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("Canvas is installed", raised.exception.code)
        self.assertIn("nothing else on the TV was changed", raised.exception.code)
        self.assertIn("this run granted it no permissions", raised.exception.code)
        self.assertEqual(self.tv.issued("pm install"), ["pm install -r /data/local/tmp/canvas.apk"])  # no -g
        self.assertEqual(self.tv.granted, set())
        for forbidden in ("pm grant", "appops set", "pm disable-user", "pm enable", "cmd package set-home-activity",
                          "settings put", "settings delete", "am force-stop", "input keyevent"):
            self.assertEqual(self.tv.issued(forbidden), [], forbidden)
        self.assertEqual(self.tv.packages, {LAUNCHERX: True, WRAITH: True})
        self.assertEqual(self.tv.home, STOCK_HOME)
        self.assertEqual(self.tv.settings[("secure", "screensaver_components")], STOCK_DREAM)
        self.assertEqual(self.backup()["disabled_by_canvas"], [])
        self.assertTrue(self.tv.closed)

    def test_no_enter_and_anything_but_yes_stop_the_install_with_only_the_app_on_the_tv(self):
        for answer in ("n", "N", "", "  ", "maybe", "yep"):
            with self.subTest(answer=answer):
                self.setUp()
                self.assert_stopped_with_only_the_app_installed(Answers(answer))

    def test_end_of_input_and_ctrl_c_stop_it_too(self):
        for answers in ((), (KeyboardInterrupt,)):
            with self.subTest(answers=answers):
                self.setUp()
                self.assert_stopped_with_only_the_app_installed(Answers(*answers))

    def test_the_apps_are_listed_one_per_line_and_asked_about_once_before_anything_is_changed(self):
        seen = []

        def ask(prompt):
            seen.append((prompt, list(self.tv.commands)))
            return "n"

        with self.assertRaises(SystemExit):
            self.run_install(ask)
        self.assertEqual([prompt for prompt, _ in seen], [self.QUESTION])
        before = seen[0][1]
        self.assertEqual(len([c for c in before if c.startswith("cmd package query-activities")]), 1)
        for forbidden in ("cmd package set-home-activity", "pm disable-user", "settings put"):
            self.assertEqual([c for c in before if c.startswith(forbidden)], [], forbidden)
        lines = self.stdout.getvalue().splitlines()
        self.assertIn(f"  {LAUNCHERX}", lines)
        self.assertIn(f"  {WRAITH}", lines)
        self.assertNotIn("  com.android.tv.settings", lines)
        self.assertNotIn("  " + canvas.PKG, lines)

    def test_y_or_yes_in_any_case_goes_on_and_makes_canvas_the_home_screen(self):
        for answer in ("y", "Y", "yes", " YES "):
            with self.subTest(answer=answer):
                self.setUp()
                ask = Answers(answer)
                self.run_install(ask)
                self.assertEqual(ask.prompts, [self.QUESTION])
                self.assertEqual(self.tv.home, canvas.HOME)
                self.assertEqual(self.tv.packages, {LAUNCHERX: False, WRAITH: False})
                self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])
                self.assertEqual(self.tv.settings[("secure", "screensaver_components")], canvas.DREAM)

    def test_yes_goes_on_without_asking_but_still_shows_the_list(self):
        self.run_install(never_asked, "--yes")
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertIn(f"  {LAUNCHERX}", self.stdout.getvalue().splitlines())

    def test_after_a_no_the_same_install_can_be_run_again_and_answered_yes(self):
        with self.assertRaises(SystemExit):
            self.run_install(Answers("n"))
        self.run_install(Answers("y"))
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertEqual(self.backup()["disabled_by_canvas"], [LAUNCHERX, WRAITH])

    def test_app_only_never_asks_and_never_switches_anything_off(self):
        self.run_install(never_asked, "--app-only")
        self.assertEqual(self.tv.issued("pm disable-user"), [])
        self.assertEqual(self.tv.issued("pm grant"), [])
        self.assertEqual(self.tv.issued("appops set"), [])
        self.assertEqual(self.tv.home, STOCK_HOME)

    def test_with_nothing_to_switch_off_there_is_no_question(self):
        self.run_install(never_asked, "--yes")  # first install: switches the two Google TV home apps off
        self.tv.commands.clear()
        self.run_install(never_asked)  # nothing left that offers itself as a home screen except Canvas
        self.assertEqual(self.tv.issued("pm disable-user"), [])
        self.assertEqual(self.tv.home, canvas.HOME)
        self.assertIn("no other home app", self.stdout.getvalue())

    def test_unknown_flags_and_extra_arguments_stop_before_the_tv_is_touched(self):
        for args in (["canvas.apk", "--force"], ["canvas.apk", "-y"], ["canvas.apk", "--yes=1"],
                     ["canvas.apk", "other.apk"]):
            with self.subTest(args=args):
                with self.assertRaises(SystemExit) as raised:
                    canvas.cmd_install(args, ask=never_asked)
                self.assertNotEqual(exit_status(raised.exception), 0)
                self.assertIn("usage: tools/canvas install", raised.exception.code)
                self.assertEqual(self.tv.commands, [])
                self.assertFalse(os.path.exists(self.backup_file))

    def test_main_passes_install_arguments_through_and_asks_through_input(self):
        with mock.patch.object(sys, "argv", ["canvas", "install", "canvas.apk"]), \
                mock.patch("builtins.input", return_value="y") as asked:
            canvas.main()
        asked.assert_called_once_with(self.QUESTION)
        self.assertEqual(self.tv.home, canvas.HOME)


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

    LET_GO = [f"pm revoke {canvas.PKG} android.permission.WRITE_SECURE_SETTINGS",
              f"appops set {canvas.PKG} WRITE_SETTINGS default",
              *(f"pm revoke {canvas.PKG} {permission}" for permission in canvas.LOCATION),
              f"am force-stop {canvas.PKG}"]

    def test_restore_takes_the_permissions_back_and_stops_canvas_before_changing_anything(self):
        canvas.cmd_restore([])
        commands = self.tv.commands
        for command in self.LET_GO[:-1]:
            self.assertEqual(commands.count(command), 1, command)
        self.assertEqual(self.tv.granted, set())
        let_go = [commands.index(command) for command in self.LET_GO]  # the first force-stop
        self.assertEqual(let_go, sorted(let_go))
        first_change = min(i for i, c in enumerate(commands) if c.startswith(("pm enable", "settings put",
                                                                              "settings delete")))
        self.assertLess(max(let_go), first_change)
        self.assertLess(max(let_go), min(i for i, c in enumerate(commands) if c.startswith("settings put")))
        self.assertIn("Restored", self.stdout.getvalue())

    def test_restore_leaves_canvas_stopped_although_android_relaunches_the_home_app_it_stops(self):
        self.assertIn(canvas.PKG, self.tv.running)
        canvas.cmd_restore([])
        self.assertNotIn(canvas.PKG, self.tv.running)
        commands = self.tv.commands
        home_back = commands.index(f"cmd package set-home-activity --user 0 {STOCK_HOME}")
        last_stop = len(commands) - 1 - commands[::-1].index(f"am force-stop {canvas.PKG}")
        self.assertLess(home_back, last_stop)
        self.assertLess(last_stop, commands.index(f"pidof {canvas.PKG}"))
        self.assertIn("Restored", self.stdout.getvalue())

    def test_restore_exits_non_zero_when_canvas_keeps_running(self):
        self.tv.ignore = ("am force-stop",)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_restore([])
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("Canvas is still running", raised.exception.code)
        self.assertNotIn("Restored", self.stdout.getvalue())

    def test_a_tv_without_pidof_does_not_fail_the_restore(self):
        self.tv.ignore = ("am force-stop",)  # Canvas would count as running, but this TV cannot say
        answer = self.tv._answer
        self.tv._answer = lambda command: ("/system/bin/sh: pidof: not found" if command.startswith("pidof")
                                           else answer(command))
        canvas.cmd_restore([])
        self.assertIn("Restored", self.stdout.getvalue())

    def test_restore_with_uninstall_removes_the_app_and_still_succeeds(self):
        canvas.cmd_restore(["--uninstall"])
        self.assertEqual(self.tv.issued("pm uninstall"), [f"pm uninstall {canvas.PKG}"])
        for command in self.LET_GO:  # uninstalling takes the permissions and stops the app anyway
            self.assertNotIn(command, self.tv.commands)
        self.assertIn("Restored", self.stdout.getvalue())

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
        self.assertEqual(data["disabled_before"], [])
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


DEVICE_LIST = textwrap.dedent("""\
    # Sony BRAVIA (BF1), Android 12
    # Apply: tools/canvas debloat devices/sony-bravia-bf1.txt

    com.loose.first            # sits above every header

    ## Sony promos
    com.sony.dtv.promos        # Sony promotional content
    com.sony.dtv.sonytab       # the "Sony" tab

    ## Google extras
    # a note about the group is a comment, not a package
    com.google.android.videos  # Google TV
    com.google.android.feedback

    ## Sony help apps
    com.sony.dtv.smarthelp     # on-screen help
    """)
PROMOS = ["com.sony.dtv.promos", "com.sony.dtv.sonytab"]
EXTRAS = ["com.google.android.videos", "com.google.android.feedback"]
HELP = ["com.sony.dtv.smarthelp"]
LOOSE = ["com.loose.first"]


class Answers:
    """Stands in for input(): hands out the typed answers, records every prompt, and hits end of input after the last.
    An exception class in the list is raised instead of returned (KeyboardInterrupt for Ctrl-C)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer
        return answer


def unknown_package(pkg, command="disable-user"):
    """pm's report for a package the TV does not have: an exception, not a `new state:` line."""
    return (f"Exception occurred while executing '{command}':\n"
            f"java.lang.IllegalArgumentException: Unknown package: {pkg}\n"
            "\tat com.android.server.pm.PackageManagerShellCommand.runSetEnabledSetting"
            "(PackageManagerShellCommand.java:1930)")


def never_asked(prompt):
    raise AssertionError(f"asked a question it should not have: {prompt!r}")


class DeviceListParsingTests(unittest.TestCase):

    def test_groups_come_back_in_file_order_with_loose_lines_in_other(self):
        groups = canvas.parse_groups(DEVICE_LIST)
        self.assertEqual([name for name, _ in groups], ["Other", "Sony promos", "Google extras", "Sony help apps"])
        self.assertEqual([[pkg for pkg, _ in packages] for _, packages in groups], [LOOSE, PROMOS, EXTRAS, HELP])

    def test_each_package_keeps_its_description_and_a_bare_package_has_none(self):
        groups = dict(canvas.parse_groups(DEVICE_LIST))
        self.assertEqual(groups["Sony promos"], [("com.sony.dtv.promos", "Sony promotional content"),
                                                 ("com.sony.dtv.sonytab", 'the "Sony" tab')])
        self.assertEqual(groups["Google extras"], [("com.google.android.videos", "Google TV"),
                                                   ("com.google.android.feedback", "")])

    def test_header_comments_blank_lines_and_notes_are_not_packages(self):
        every = [pkg for _, packages in canvas.parse_groups(DEVICE_LIST) for pkg, _ in packages]
        self.assertEqual(every, LOOSE + PROMOS + EXTRAS + HELP)

    def test_a_list_with_no_headers_is_one_group_called_other(self):
        groups = canvas.parse_groups("# just a list\ncom.a.b  # first\ncom.c.d\n")
        self.assertEqual(groups, [("Other", [("com.a.b", "first"), ("com.c.d", "")])])

    def test_the_same_group_name_twice_is_one_group_and_a_repeated_package_counts_once(self):
        groups = canvas.parse_groups("## Sony\ncom.a.b\n## Google\ncom.c.d\n## sony\ncom.e.f\ncom.a.b\n")
        self.assertEqual(groups, [("Sony", [("com.a.b", ""), ("com.e.f", "")]), ("Google", [("com.c.d", "")])])

    def test_a_group_with_no_packages_is_dropped(self):
        self.assertEqual(canvas.parse_groups("## Empty\n## Full\ncom.a.b\n"), [("Full", [("com.a.b", "")])])

    def test_something_that_is_not_a_package_name_is_refused_with_its_line_number(self):
        for bad in ("rm -rf /", "com.a.b com.c.d", "nodots", "com.a.b;reboot"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit) as raised:
                canvas.parse_groups(f"## G\ncom.fine.one\n{bad}\n", "list.txt")
            self.assertIn("list.txt line 3", raised.exception.code)

    def test_a_header_without_a_name_is_refused(self):
        with self.assertRaises(SystemExit) as raised:
            canvas.parse_groups("##\ncom.a.b\n")
        self.assertIn("needs a name", raised.exception.code)

    def test_find_group_ignores_case_and_lists_the_names_when_there_is_no_match(self):
        groups = canvas.parse_groups(DEVICE_LIST)
        self.assertEqual(canvas.find_group(groups, "  SONY promos ")[0], "Sony promos")
        with self.assertRaises(SystemExit) as raised:
            canvas.find_group(groups, "Sony")
        self.assertIn("'Sony promos'", raised.exception.code)
        self.assertIn("'Google extras'", raised.exception.code)

    def test_the_committed_sony_list_has_the_four_groups_the_owner_was_shown(self):
        path = os.path.join(TOOLS, os.pardir, "devices", "sony-bravia-bf1.txt")
        groups = canvas.read_groups(path)
        self.assertEqual([name for name, _ in groups], ["Sony promos", "Google extras", "Sony help apps", "BRAVIA CORE"])
        self.assertEqual([[pkg for pkg, _ in packages] for _, packages in groups], [
            ["com.sony.dtv.promos", "com.sony.dtv.sonytab", "com.sony.dtv.sonyselect",
             "android.autoinstalls.config.sony.bluefin", "com.sony.dtv.demosupport", "com.sony.dtv.demosystemsupport",
             "com.sony.dtv.multiscreendemo"],
            ["com.google.android.videos", "com.google.android.play.games", "com.google.android.feedback"],
            ["com.sony.dtv.smarthelp", "com.sony.dtv.ecodashboard", "com.sony.dtv.notificationcenter",
             "com.sony.dtv.m2.seeds.iot"],
            ["com.sonypicturescore"]])
        self.assertTrue(all(description for _, packages in groups for _, description in packages),
                        "every package in the committed list should say what it is")

    def test_the_committed_generic_google_tv_list_is_one_group_of_google_extras(self):
        groups = canvas.read_groups(os.path.join(TOOLS, os.pardir, "devices", "google-tv-common.txt"))
        self.assertEqual([name for name, _ in groups], ["Google extras"])
        self.assertEqual([pkg for pkg, _ in groups[0][1]],
                         ["com.google.android.videos", "com.google.android.play.games", "com.google.android.feedback"])

    def test_every_committed_device_list_parses_and_explains_each_package(self):
        folder = os.path.join(TOOLS, os.pardir, "devices")
        lists = sorted(name for name in os.listdir(folder) if name.endswith(".txt"))
        self.assertIn("google-tv-common.txt", lists)
        self.assertIn("sony-bravia-bf1.txt", lists)
        never = ("com.android.tv.settings", "com.android.settings", "com.android.vending", "com.google.android.gms",
                 "com.google.android.webview", "com.google.android.katniss", "com.google.android.inputmethod.latin")
        for name in lists:
            groups = canvas.read_groups(os.path.join(folder, name))
            for _, packages in groups:
                for pkg, description in packages:
                    self.assertTrue(description, f"{name}: {pkg} has no description")
                    self.assertNotIn(pkg, never + tuple(canvas.KEEP_HOMES), f"{name}: {pkg} must never be in a list")

    def test_a_missing_list_file_is_a_message_not_a_traceback(self):
        with self.assertRaises(SystemExit) as raised:
            canvas.read_groups(os.path.join(TOOLS, "no-such-list.txt"))
        self.assertIn("Can't read", raised.exception.code)


class DebloatArgumentTests(unittest.TestCase):

    def test_a_list_alone_means_ask_for_each_group(self):
        self.assertEqual(canvas.parse_debloat_args(["list.txt"]), ("list.txt", False, None))

    def test_yes_and_group_are_understood_wherever_they_come(self):
        self.assertEqual(canvas.parse_debloat_args(["list.txt", "--yes"]), ("list.txt", True, None))
        self.assertEqual(canvas.parse_debloat_args(["--group", "Sony promos", "list.txt"]),
                         ("list.txt", False, "Sony promos"))

    def test_unknown_flags_are_rejected(self):
        for flag in ("--force", "-y", "--group=Sony promos", "--Yes"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as raised:
                canvas.parse_debloat_args(["list.txt", flag])
            self.assertIn("Unknown option", raised.exception.code)

    def test_group_needs_a_name_and_cannot_be_combined_with_yes(self):
        for args in (["list.txt", "--group"], ["list.txt", "--group", "--yes"]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                canvas.parse_debloat_args(args)
            self.assertIn("--group needs a group name", raised.exception.code)
        with self.assertRaises(SystemExit) as raised:
            canvas.parse_debloat_args(["list.txt", "--yes", "--group", "Sony promos"])
        self.assertIn("not both", raised.exception.code)

    def test_a_second_list_or_no_list_is_rejected(self):
        for args in (["a.txt", "b.txt"], ["--yes"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                canvas.parse_debloat_args(args)


class DebloatPromptTests(TvTestCase):
    """debloat and undebloat show each group, ask, and touch the TV only for the groups answered yes."""

    def setUp(self):
        super().setUp()
        self.list = os.path.join(os.path.dirname(canvas.BACKUPS), "device.txt")
        os.makedirs(os.path.dirname(self.list), exist_ok=True)
        with open(self.list, "w") as f:
            f.write(DEVICE_LIST)
        self.connects = 0

        def connect():
            self.connects += 1
            return self.tv

        self.patch(canvas, "connect", connect)

    def disabled(self):
        return [c.split()[-1] for c in self.tv.issued("pm disable-user")]

    def test_y_applies_a_group_and_enter_or_n_skips_it(self):
        ask = Answers("n", "y", "", "n")  # Other: no, Sony promos: yes, Google extras: Enter, Sony help apps: no
        canvas.cmd_debloat([self.list], ask=ask)
        self.assertEqual(self.disabled(), PROMOS)
        self.assertEqual(ask.prompts, ["Disable this group? [y/N] "] * 4)

    def test_yes_in_any_case_counts_and_anything_else_is_no(self):
        canvas.cmd_debloat([self.list], ask=Answers("YES", "  Y ", "maybe", "no"))
        self.assertEqual(self.disabled(), LOOSE + PROMOS)

    def test_pressing_enter_on_every_group_leaves_the_tv_alone(self):
        canvas.cmd_debloat([self.list], ask=Answers("", "", "", ""))
        self.assertEqual(self.tv.commands, [])
        self.assertIn("The TV was not touched", self.stdout.getvalue())

    def test_each_group_is_shown_with_its_packages_and_what_they_are_before_the_question(self):
        canvas.cmd_debloat([self.list], ask=Answers("", "", "", ""))
        shown = self.stdout.getvalue()
        for text in ("Sony promos", "com.sony.dtv.promos", "Sony promotional content", "Google extras",
                     "com.google.android.feedback", "Other", "com.loose.first"):
            self.assertIn(text, shown)

    def test_yes_applies_every_group_without_asking(self):
        canvas.cmd_debloat([self.list, "--yes"], ask=never_asked)
        self.assertEqual(self.disabled(), LOOSE + PROMOS + EXTRAS + HELP)

    def test_group_applies_exactly_that_group_without_asking(self):
        canvas.cmd_debloat([self.list, "--group", "Sony promos"], ask=never_asked)
        self.assertEqual(self.disabled(), PROMOS)
        self.assertEqual(self.tv.issued("pm enable"), [])

    def test_group_matches_the_name_whatever_the_case(self):
        canvas.cmd_debloat([self.list, "--group", "google EXTRAS"], ask=never_asked)
        self.assertEqual(self.disabled(), EXTRAS)

    def test_an_unknown_group_stops_before_the_tv_is_touched(self):
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list, "--group", "Sony"], ask=never_asked)
        self.assertIn("No group named 'Sony'", raised.exception.code)
        self.assertEqual(self.tv.commands, [])

    def test_an_unknown_flag_stops_before_the_list_is_read_or_the_tv_is_touched(self):
        with self.assertRaises(SystemExit):
            canvas.cmd_debloat([self.list, "--force"], ask=never_asked)
        self.assertEqual(self.tv.commands, [])

    def assert_cancelled(self, ask):
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list], ask=ask)
        self.assertNotEqual(exit_status(raised.exception), 0)
        self.assertIn("Cancelled", raised.exception.code)
        self.assertIn("--yes", raised.exception.code)  # says how to apply without questions
        self.assertEqual(self.tv.commands, [])
        self.assertEqual(self.connects, 0)  # the TV was never even contacted

    def test_end_of_input_at_the_first_question_cancels_the_run(self):
        self.assert_cancelled(Answers())

    def test_end_of_input_after_a_yes_cancels_the_whole_run_and_applies_nothing(self):
        self.assert_cancelled(Answers("n", "y"))  # Sony promos was answered yes, then Ctrl-D

    def test_ctrl_c_at_any_question_cancels_the_run_too(self):
        self.assert_cancelled(Answers(KeyboardInterrupt))
        self.assert_cancelled(Answers("y", "y", KeyboardInterrupt))

    def test_undebloat_cancels_the_same_way(self):
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list], enable=True, ask=Answers("y", KeyboardInterrupt))
        self.assertIn("Cancelled", raised.exception.code)
        self.assertEqual((self.tv.commands, self.connects), ([], 0))

    def test_undebloat_asks_to_re_enable_and_enables_only_the_groups_answered_yes(self):
        for pkg in LOOSE + PROMOS + EXTRAS + HELP:
            self.tv.packages[pkg] = False
        ask = Answers("n", "", "y", "n")
        canvas.cmd_debloat([self.list], enable=True, ask=ask)
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {pkg}" for pkg in EXTRAS])
        self.assertEqual(self.tv.issued("pm disable"), [])
        self.assertEqual(ask.prompts, ["Re-enable this group? [y/N] "] * 4)
        self.assertEqual([pkg for pkg, enabled in self.tv.packages.items() if enabled and pkg not in (LAUNCHERX, WRAITH)],
                         EXTRAS)

    def test_undebloat_group_and_yes(self):
        canvas.cmd_debloat([self.list, "--group", "Sony help apps"], enable=True, ask=never_asked)
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {pkg}" for pkg in HELP])
        canvas.cmd_debloat([self.list, "--yes"], enable=True, ask=never_asked)
        self.assertEqual(len(self.tv.issued("pm enable")), len(HELP) + len(LOOSE + PROMOS + EXTRAS + HELP))

    def test_every_pm_call_is_its_own_shell_call(self):
        canvas.cmd_debloat([self.list, "--yes"], ask=never_asked)
        canvas.cmd_debloat([self.list, "--yes"], enable=True, ask=never_asked)
        self.assertEqual(len(self.tv.commands), 2 * len(LOOSE + PROMOS + EXTRAS + HELP))
        for command in self.tv.commands:
            self.assertRegex(command, r"^pm (disable-user --user 0|enable) [A-Za-z0-9_.]+$")

    def test_the_connection_is_closed_afterwards(self):
        canvas.cmd_debloat([self.list, "--group", "Sony promos"], ask=never_asked)
        self.assertTrue(self.tv.closed)

    def test_the_closing_line_names_what_was_done_and_what_was_left_alone(self):
        canvas.cmd_debloat([self.list, "--group", "Sony promos"], ask=never_asked)
        self.assertIn("Disabled: Sony promos. Left alone: Other, Google extras, Sony help apps.", self.stdout.getvalue())

    def test_a_package_pm_rejects_is_reported_not_called_disabled_and_the_run_goes_on(self):
        self.tv.pm_errors[PROMOS[0]] = unknown_package(PROMOS[0])
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list, "--group", "Sony promos"], ask=never_asked)
        self.assertEqual(exit_status(raised.exception), 1)
        self.assertEqual(self.disabled(), PROMOS)  # it still went on to the next package
        self.assertFalse(self.tv.packages[PROMOS[1]])
        shown = self.stdout.getvalue()
        self.assertIn("Disabled with problems: Sony promos (com.sony.dtv.promos: Exception occurred while executing "
                      "'disable-user': java.lang.IllegalArgumentException: Unknown package: com.sony.dtv.promos)", shown)
        self.assertNotIn("Disabled: Sony promos", shown)

    def test_groups_that_worked_and_groups_with_problems_are_listed_apart(self):
        self.tv.pm_errors[EXTRAS[1]] = "java.lang.SecurityException: Shell cannot change component state"
        self.tv.pm_errors[HELP[0]] = ""  # pm said nothing at all
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list, "--yes"], ask=never_asked)
        self.assertEqual(exit_status(raised.exception), 1)
        self.assertEqual(self.disabled(), LOOSE + PROMOS + EXTRAS + HELP)
        self.assertIn("Disabled: Other, Sony promos. Disabled with problems: "
                      "Google extras (com.google.android.feedback: java.lang.SecurityException: Shell cannot change "
                      "component state), Sony help apps (com.sony.dtv.smarthelp: no output from pm).",
                      self.stdout.getvalue())

    def test_several_failures_in_one_group_are_all_named(self):
        for pkg in PROMOS:
            self.tv.pm_errors[pkg] = f"Error: Unknown package: {pkg}"
        with self.assertRaises(SystemExit):
            canvas.cmd_debloat([self.list, "--group", "sony promos"], ask=never_asked)
        self.assertIn("Sony promos (com.sony.dtv.promos: Error: Unknown package: com.sony.dtv.promos; "
                      "com.sony.dtv.sonytab: Error: Unknown package: com.sony.dtv.sonytab)", self.stdout.getvalue())

    def test_a_pm_that_answers_nothing_is_a_failure_too(self):
        self.tv.ignore = ("pm disable-user",)
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list, "--group", "Sony help apps"], ask=never_asked)
        self.assertEqual(exit_status(raised.exception), 1)
        self.assertIn("Disabled with problems: Sony help apps (com.sony.dtv.smarthelp: no output from pm)",
                      self.stdout.getvalue())

    def test_failures_after_answering_questions_still_exit_non_zero_and_name_what_was_left_alone(self):
        self.tv.pm_errors[PROMOS[1]] = unknown_package(PROMOS[1])
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list], ask=Answers("n", "y", "", "n"))
        self.assertEqual(exit_status(raised.exception), 1)
        self.assertIn("Left alone: Other, Google extras, Sony help apps.", self.stdout.getvalue())

    def test_undebloat_checks_for_new_state_enabled_and_reports_the_rest(self):
        for pkg in LOOSE + PROMOS + EXTRAS + HELP:
            self.tv.packages[pkg] = False
        self.tv.pm_errors[PROMOS[0]] = unknown_package(PROMOS[0], "enable")
        with self.assertRaises(SystemExit) as raised:
            canvas.cmd_debloat([self.list, "--yes"], enable=True, ask=never_asked)
        self.assertEqual(exit_status(raised.exception), 1)
        self.assertEqual(len(self.tv.issued("pm enable")), len(LOOSE + PROMOS + EXTRAS + HELP))
        self.assertFalse(self.tv.packages[PROMOS[0]])
        self.assertTrue(self.tv.packages[PROMOS[1]])
        shown = self.stdout.getvalue()
        self.assertIn("Re-enabled: Other, Google extras, Sony help apps. Re-enabled with problems: Sony promos "
                      "(com.sony.dtv.promos: Exception occurred while executing 'enable': "
                      "java.lang.IllegalArgumentException: Unknown package: com.sony.dtv.promos)", shown)
        self.assertNotIn("Disabled", shown)

    def test_a_disable_answer_is_not_accepted_as_an_enable(self):
        self.tv.pm_errors[PROMOS[0]] = f"Package {PROMOS[0]} new state: disabled-user"  # pm says the wrong state
        with self.assertRaises(SystemExit):
            canvas.cmd_debloat([self.list, "--group", "Sony promos"], enable=True, ask=never_asked)
        self.assertIn("Re-enabled with problems: Sony promos", self.stdout.getvalue())

    def test_main_exits_non_zero_when_a_package_failed(self):
        self.tv.pm_errors[PROMOS[0]] = unknown_package(PROMOS[0])
        with mock.patch.object(sys, "argv", ["canvas.py", "debloat", self.list, "--group", "Sony promos"]), \
                self.assertRaises(SystemExit) as raised:
            canvas.main()
        self.assertEqual(exit_status(raised.exception), 1)

    def test_main_routes_debloat_and_undebloat_and_asks_through_input(self):
        with mock.patch.object(sys, "argv", ["canvas.py", "debloat", self.list]), \
                mock.patch("builtins.input", side_effect=["n", "y", "n", "n"]) as typed:
            canvas.main()
        self.assertEqual(self.disabled(), PROMOS)
        self.assertEqual(typed.call_count, 4)
        with mock.patch.object(sys, "argv", ["canvas.py", "undebloat", self.list, "--group", "Sony promos"]):
            canvas.main()
        self.assertEqual(self.tv.issued("pm enable"), [f"pm enable {pkg}" for pkg in PROMOS])


class SameComponentTests(unittest.TestCase):
    def test_short_and_full_class_names_match(self):
        self.assertTrue(canvas.same_component(canvas.HOME, canvas.PKG + "/" + canvas.PKG + ".home.HomeActivity"))
        self.assertTrue(canvas.same_component(STOCK_HOME, STOCK_HOME))
        self.assertFalse(canvas.same_component(STOCK_HOME, canvas.HOME))
        self.assertFalse(canvas.same_component("", canvas.HOME))
        self.assertTrue(canvas.same_component("", ""))


class AdbKeyTests(unittest.TestCase):
    """Whoever can read tools/adbkey/adbkey can drive the TV, so the key is the owner's alone."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = os.path.join(tmp.name, "adbkey")
        self.key = os.path.join(self.folder, "adbkey")
        for name, value in (("KEY", self.key), ("keygen", self.fake_keygen),
                            ("PythonRSASigner", lambda public, private: (public, private))):
            patcher = mock.patch.object(canvas, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def fake_keygen(path):
        """Writes the pair the way adb-shell's keygen does: plain open(), so the umask decides (usually 0644)."""
        for name, text in ((path, "PRIVATE"), (path + ".pub", "PUBLIC")):
            with open(name, "w") as f:
                f.write(text)
            os.chmod(name, 0o644)

    def mode(self, path):
        return os.stat(path).st_mode & 0o777

    def test_a_new_key_is_made_in_a_0700_folder_and_is_0600(self):
        self.assertEqual(canvas.signer(), ("PUBLIC", "PRIVATE"))
        self.assertEqual(self.mode(self.folder), 0o700)
        self.assertEqual(self.mode(self.key), 0o600)

    def test_a_key_left_readable_by_an_older_version_is_tightened(self):
        os.makedirs(self.folder)
        os.chmod(self.folder, 0o755)
        self.fake_keygen(self.key)
        canvas.signer()
        self.assertEqual(self.mode(self.folder), 0o700)
        self.assertEqual(self.mode(self.key), 0o600)
        with open(self.key) as f:
            self.assertEqual(f.read(), "PRIVATE")  # the same key: the TV's "Allow USB debugging?" stays accepted

    def test_a_key_that_is_already_private_is_left_alone(self):
        canvas.signer()
        with mock.patch.object(canvas.os, "chmod") as chmod:
            canvas.signer()
        chmod.assert_not_called()


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
