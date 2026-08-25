#!/usr/bin/env python3
"""Installing the app must refuse while the nightly is running.

Since 2026-08-21 the nightly runs FROM the bundle, and the relaunch that
follows every install kills the app's python workers. On 2026-08-24 an install
at 12:57:30 landed while the run was on DE: three steps died on SIGTERM —
daily_metrics, backfill_daily, backfill_target_daily — so DE's Monday
attribution true-up never ran. Nothing raised. DE's Dashboard just showed sales
about 15% low, and it was found by comparing two tables that are meant to agree.

The guard is exercised here rather than read. `pgrep` is stubbed on PATH in
both directions, because a guard that refuses everything passes a one-sided
test exactly as happily as one that refuses nothing.

Run from the Ads folder:  python3 -m unittest tests.packaging_nightly_guard_tests -v
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(HERE, "scripts", "package_app.sh")
NIGHTLY_MESSAGE = "the nightly is running"


def _stub_dir(pgrep_finds_a_run):
    """A PATH entry whose `pgrep` answers a fixed way, plus stubs for the tools
    the preflight checks BEFORE the guard — otherwise the script exits on a
    missing xcodebuild and never reaches the line under test."""
    d = tempfile.mkdtemp(prefix="pkgguard-")
    hit = 0 if pgrep_finds_a_run else 1
    scripts = {
        # printing a pid keeps `$(pgrep ... | head -1)` non-empty
        "pgrep": f'#!/bin/bash\n[ {hit} -eq 0 ] && echo 99999\nexit {hit}\n',
        # reached only when the guard lets the run through; fails immediately so
        # the test never triggers a real two-minute build
        "xcodebuild": '#!/bin/bash\necho "stub xcodebuild refuses" >&2\nexit 70\n',
        "codesign": '#!/bin/bash\nexit 0\n',
        "plutil": '#!/bin/bash\nexit 0\n',
    }
    for name, body in scripts.items():
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return d


def _run(install, pgrep_finds_a_run, force=False):
    d = _stub_dir(pgrep_finds_a_run)
    env = dict(os.environ)
    env["PATH"] = d + os.pathsep + env.get("PATH", "")
    env["SKIP_SWIFT_TESTS"] = "1"
    if force:
        env["FORCE_DURING_NIGHTLY"] = "1"
    args = ["bash", SCRIPT] + (["--install"] if install else [])
    r = subprocess.run(args, capture_output=True, text=True, env=env,
                       cwd=HERE, timeout=180)
    return r.returncode, r.stdout + r.stderr


class InstallingDuringTheNightlyIsRefused(unittest.TestCase):

    def test_it_refuses_when_a_run_is_in_flight(self):
        code, out = _run(install=True, pgrep_finds_a_run=True)
        self.assertNotEqual(code, 0)
        self.assertIn(NIGHTLY_MESSAGE, out)

    def test_it_says_how_to_wait_and_how_to_force(self):
        """A refusal with no way forward gets forced blindly the next time."""
        _code, out = _run(install=True, pgrep_finds_a_run=True)
        self.assertIn("FORCE_DURING_NIGHTLY=1", out)
        self.assertIn("run_scheduled.sh", out)

    def test_it_refuses_before_deleting_the_installed_bundle(self):
        """The script removes dist/ and the derived data early. Refusing after
        that would leave the tree worse than it found it."""
        _code, out = _run(install=True, pgrep_finds_a_run=True)
        self.assertNotIn("stub xcodebuild", out,
                         "the guard must fire before the build starts")

    def test_a_quiet_machine_is_not_blocked(self):
        """The other direction. Without this, a guard that always refuses looks
        identical to one that works."""
        _code, out = _run(install=True, pgrep_finds_a_run=False)
        self.assertNotIn(NIGHTLY_MESSAGE, out)

    def test_force_gets_through_a_running_nightly(self):
        _code, out = _run(install=True, pgrep_finds_a_run=True, force=True)
        self.assertNotIn(NIGHTLY_MESSAGE, out)

    def test_a_plain_build_is_never_blocked(self):
        """Only --install replaces what the nightly is reading."""
        _code, out = _run(install=False, pgrep_finds_a_run=True)
        self.assertNotIn(NIGHTLY_MESSAGE, out)


class TheFreshnessHookDoesNotDemandTheImpossible(unittest.TestCase):
    """The Stop hook and the packaging guard must not contradict each other.

    The hook blocks turn-end while /Applications is stale, and the guard now
    refuses to install while the nightly is running. Together, unmodified, they
    make the turn unendable for the several hours a run takes, on an
    instruction that cannot be followed. A block nobody can satisfy is a block
    everyone learns to ignore, including the ones that matter.

    So during a run the hook reports and steps aside. The rule is not weakened:
    the run ends and the next turn blocks again with nothing changed. Both
    directions are exercised, because a hook that never blocks looks exactly
    like this one from the passing side.
    """

    HOOK = os.path.join(HERE, ".claude", "hooks", "check_app_fresh.sh")

    def _run(self, nightly_running):
        d = tempfile.mkdtemp(prefix="hookguard-")
        hit = 0 if nightly_running else 1
        pg = os.path.join(d, "pgrep")
        with open(pg, "w") as f:
            f.write(f'#!/bin/bash\n[ {hit} -eq 0 ] && echo 1234\nexit {hit}\n')
        os.chmod(pg, os.stat(pg).st_mode | stat.S_IEXEC)
        env = dict(os.environ)
        env["PATH"] = d + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PROJECT_DIR"] = HERE
        r = subprocess.run(["bash", self.HOOK], capture_output=True, text=True,
                           env=env, cwd=HERE, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def test_it_steps_aside_while_a_run_is_in_flight(self):
        code, out = self._run(nightly_running=True)
        self.assertEqual(code, 0, "blocking here cannot be satisfied")
        if out.strip():
            self.assertNotIn("BLOCK:", out)

    @staticmethod
    def _is_stale():
        """Ask the hasher and the manifest directly.

        The first version of the test below decided this from the hook's OWN
        output — `if "stale" not in out: skipTest(...)` — so silencing the
        notice made the test SKIP instead of fail. Proved on 2026-08-24 by
        doing exactly that: two mutations of this hook were caught and that one
        was not. Never let the thing under test decide whether to run the
        assertion.
        """
        hasher = os.path.join(HERE, ".claude", "hooks", "app_src_hash.sh")
        manifest = "/Applications/Merch Ads.app/Contents/Resources/.src_manifest"
        if not (os.access(hasher, os.X_OK) and os.path.exists(manifest)):
            return None
        env = dict(os.environ, CLAUDE_PROJECT_DIR=HERE)
        cur = subprocess.run(["bash", hasher], capture_output=True, text=True,
                             env=env, cwd=HERE, timeout=120).stdout.strip()
        with open(manifest) as f:
            return bool(cur) and cur != f.read().strip()

    def test_stepping_aside_still_says_the_app_is_stale(self):
        """Silence would read as 'nothing to do' and the install would be
        forgotten the moment the run finished."""
        stale = self._is_stale()
        if stale is None:
            self.skipTest("no manifest or hasher on this machine")
        _code, out = self._run(nightly_running=True)
        if not stale:
            self.assertNotIn("stale", out.lower(),
                             "a fresh app must not be reported stale")
            return
        self.assertIn("stale", out.lower(),
                      "the app IS stale and the notice must say so")
        self.assertIn("nightly is running", out)

    def test_a_quiet_machine_is_still_blocked_when_stale(self):
        """The same anti-pattern, four lines below the helper written for it.

        This used to skip on `if code == 0`, which is the hook's own answer to
        the question under test. A hook that regressed to letting everything
        through would SKIP here rather than fail. Ask the hasher instead, and
        then assert in BOTH directions — a hook that blocks unconditionally is
        just as broken and passes a one-sided test just as happily.
        """
        stale = self._is_stale()
        if stale is None:
            self.skipTest("no manifest or hasher on this machine")
        code, out = self._run(nightly_running=False)
        if not stale:
            self.assertEqual(code, 0,
                             "the tree matches the installed app — nothing to "
                             f"block, yet the hook said:\n{out}")
            return
        self.assertEqual(code, 2, f"the app IS stale and was not blocked:\n{out}")
        self.assertIn("BLOCK:", out)


# --------------------------------------------------------------------------
# The guard has to be asked TWICE.
# --------------------------------------------------------------------------

def _write(path, body, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


FAKE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>io.github.zdufs.MerchAds</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleExecutable</key><string>Merch Ads</string>
</dict>
</plist>
"""


class TheInstallStepAsksAgainAfterTheBuild(unittest.TestCase):
    """The nightly can start DURING the build, and the build takes minutes.

    The preflight check answers a question about 09:55 and the bundle is
    replaced at 10:20. launchd fires at 10:00, so an install begun on a quiet
    machine sailed straight through the guard and deleted the bundle the run
    was reading from — the same damage the guard exists to prevent, through the
    window it left open.

    So the whole script is driven here, not just its first hundred lines.
    `pgrep` flips from quiet to busy after its first answer, `xcodebuild` and
    `uv` fabricate what the later steps insist on, and `rm` REFUSES anything
    under /Applications. That last stub is what makes running this safe: on the
    unfixed script the run reaches the destructive line, and the stub turns
    that into a non-zero exit and a marker file instead of a replaced app.
    """

    def _stubs(self, pgrep_flips):
        d = tempfile.mkdtemp(prefix="pkglate-")
        counter = os.path.join(d, "pgrep.count")
        marker = os.path.join(d, "applications.touched")
        # First answer quiet, every answer after it busy — the 10:00 boundary.
        # Without the flip this is just the preflight test again.
        flip = "1" if pgrep_flips else "0"
        _write(os.path.join(d, "pgrep"), f"""#!/bin/bash
n=$(cat {counter!r} 2>/dev/null || echo 0)
n=$((n + 1)); echo "$n" > {counter!r}
if [ {flip} -eq 1 ] && [ "$n" -gt 1 ]; then echo 99999; exit 0; fi
exit 1
""", True)
        _write(os.path.join(d, "xcodebuild"), """#!/bin/bash
echo "stub xcodebuild ran" >&2
derived=""
prev=""
for a in "$@"; do
  [ "$prev" = "-derivedDataPath" ] && derived="$a"
  prev="$a"
done
[ -n "$derived" ] || exit 0
app="$derived/Build/Products/Release/Merch Ads.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cat > "$app/Contents/Info.plist" <<'PLIST'
""" + FAKE_PLIST + """PLIST
printf '#!/bin/bash\\nexit 0\\n' > "$app/Contents/MacOS/Merch Ads"
chmod +x "$app/Contents/MacOS/Merch Ads"
: > "$app/Contents/Resources/AppIcon.icns"
exit 0
""", True)
        _write(os.path.join(d, "uv"), """#!/bin/bash
dir=""
prev=""
for a in "$@"; do
  [ "$prev" = "--install-dir" ] && dir="$a"
  prev="$a"
done
[ -n "$dir" ] || exit 0
py="$dir/cpython-3.12.9-macos-stub/bin/python3"
mkdir -p "$(dirname "$py")"
cat > "$py" <<'STUBPY'
#!/bin/bash
for a in "$@"; do
  case "$a" in *sys.version*) echo "3.12.9"; exit 0;; esac
done
exit 0
STUBPY
chmod +x "$py"
exit 0
""", True)
        # The safety net. /Applications is never written by this test.
        _write(os.path.join(d, "rm"), f"""#!/bin/bash
for a in "$@"; do
  case "$a" in
    /Applications*) echo "$a" >> {marker!r}; echo "stub rm refused $a" >&2; exit 66;;
  esac
done
exec /bin/rm "$@"
""", True)
        for name in ("codesign", "plutil"):
            _write(os.path.join(d, name), "#!/bin/bash\nexit 0\n", True)
        return d, marker

    def _run(self, pgrep_flips):
        d, marker = self._stubs(pgrep_flips)
        home = tempfile.mkdtemp(prefix="pkghome-")
        tmp = tempfile.mkdtemp(prefix="pkgtmp-")
        env = dict(os.environ)
        env["PATH"] = d + os.pathsep + env.get("PATH", "")
        env["SKIP_SWIFT_TESTS"] = "1"
        env["HOME"] = home          # keeps the CPython cache out of the real one
        env["TMPDIR"] = tmp         # keeps the derived data out of the real one
        r = subprocess.run(["bash", SCRIPT, "--install"], capture_output=True,
                           text=True, env=env, cwd=HERE, timeout=600)
        return r.returncode, r.stdout + r.stderr, marker

    def tearDown(self):
        built = os.path.join(HERE, "dist", "Merch Ads.app")
        if os.path.isdir(built):
            shutil.rmtree(built, ignore_errors=True)

    def test_a_nightly_that_starts_during_the_build_still_stops_the_install(self):
        code, out, marker = self._run(pgrep_flips=True)
        self.assertIn("stub xcodebuild ran", out,
                      "the run must get past the build, or this is only the "
                      "preflight check again")
        self.assertNotEqual(code, 0)
        self.assertIn(NIGHTLY_MESSAGE, out)
        self.assertFalse(os.path.exists(marker),
                         "the installed bundle was reached: " + out[-2000:])

    def test_a_machine_that_stays_quiet_still_reaches_the_install(self):
        """The other direction. A second check that always refuses would pass
        the test above and make every install impossible."""
        _code, out, marker = self._run(pgrep_flips=False)
        self.assertNotIn(NIGHTLY_MESSAGE, out)
        self.assertTrue(os.path.exists(marker),
                        "the install step was never reached: " + out[-2000:])


# --------------------------------------------------------------------------
# The hash the freshness hook compares.
# --------------------------------------------------------------------------

HASHER = os.path.join(HERE, ".claude", "hooks", "app_src_hash.sh")


class TheSourceHashIsStableAndComplete(unittest.TestCase):
    """Two ways this hash lied, both silent.

    It sorted in the caller's LOCALE, so the same untouched tree hashed one way
    under LC_ALL=C and another under en_US.UTF-8. package_app.sh stamps the
    bundle with the locale the install ran under; the Stop hook recomputes
    under the locale the turn ran under. A byte-identical fresh install then
    reads STALE for good — the false alarm the content hash replaced mtimes to
    remove.

    And it covered no build inputs. project.pbxproj decides which sources
    compile and with which settings, requirements.txt is installed into the
    bundled interpreter; changing either alone left the digest identical and
    an out-of-date /Applications copy was called fresh.
    """

    NAMES = ["A_b.swift", "Ab.swift", "_b.swift", "a-b.swift"]

    def _tree(self):
        root = tempfile.mkdtemp(prefix="srchash-")
        for i, name in enumerate(self.NAMES):
            _write(os.path.join(root, "MerchAds", name), f"// swift {i}\n")
        _write(os.path.join(root, "MerchAds.xcodeproj", "project.pbxproj"),
               "// objects = { }\n")
        _write(os.path.join(root, "MerchAds.xcodeproj", "xcshareddata",
                            "xcschemes", "MerchAds.xcscheme"), "<Scheme/>\n")
        _write(os.path.join(root, "engine", "appctl.py"), "print(1)\n")
        _write(os.path.join(root, "run_scheduled.sh"), "#!/bin/bash\n")
        _write(os.path.join(root, "run_stream_drain.sh"), "#!/bin/bash\n")
        _write(os.path.join(root, "requirements.txt"), "requests>=2.31\n")
        _write(os.path.join(root, "README.md"), "not shipped\n")
        return root

    @staticmethod
    def _hash(root, locale=None):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if locale:
            env["LC_ALL"] = locale
        r = subprocess.run(["bash", HASHER, root], capture_output=True,
                           text=True, env=env, timeout=180)
        return r.stdout.strip()

    def test_it_produces_a_digest_at_all(self):
        """Every assertion below compares two digests. Two empty strings would
        satisfy the equality one on their own."""
        digest = self._hash(self._tree())
        self.assertRegex(digest, r"^[0-9a-f]{40}$")

    def _collation_differs(self, other):
        """Ask `sort` itself whether this machine can tell the two apart.

        An environment fact, not the hook's own answer: on a box with no UTF-8
        locale the two orders are identical and the test below would pass
        without proving anything.
        """
        names = b"\0".join(("./MerchAds/" + n).encode() for n in self.NAMES) + b"\0"
        orders = []
        for loc in ("C", other):
            r = subprocess.run(["sort", "-z"], input=names, capture_output=True,
                               env=dict(os.environ, LC_ALL=loc), timeout=60)
            orders.append(r.stdout)
        return orders[0] != orders[1]

    def test_two_locales_hash_the_same_tree_the_same(self):
        other = "en_US.UTF-8"
        if not self._collation_differs(other):
            self.skipTest(f"this machine sorts {other} exactly like C")
        root = self._tree()
        self.assertEqual(self._hash(root, "C"), self._hash(root, other))

    def _changed(self, relpath, extra):
        root = self._tree()
        before = self._hash(root)
        with open(os.path.join(root, relpath), "a", encoding="utf-8") as f:
            f.write(extra)
        return before, self._hash(root)

    def test_a_swift_change_moves_the_digest(self):
        """The control. Without it, a hasher that always printed the same
        string would pass the tests below by failing to notice anything."""
        before, after = self._changed("MerchAds/Ab.swift", "// edited\n")
        self.assertNotEqual(before, after)

    def test_the_project_file_moves_the_digest(self):
        before, after = self._changed("MerchAds.xcodeproj/project.pbxproj",
                                      "// a new build phase\n")
        self.assertNotEqual(before, after,
                            "project.pbxproj decides what compiles into the bundle")

    def test_a_shared_scheme_moves_the_digest(self):
        before, after = self._changed(
            "MerchAds.xcodeproj/xcshareddata/xcschemes/MerchAds.xcscheme",
            "<!-- edited -->\n")
        self.assertNotEqual(before, after)

    def test_requirements_moves_the_digest(self):
        before, after = self._changed("requirements.txt", "urllib3>=2\n")
        self.assertNotEqual(before, after,
                            "requirements.txt is installed into the bundled "
                            "interpreter, so it changes what ships")

    def test_a_file_the_bundle_never_ships_leaves_it_alone(self):
        """The set is a list, not the whole tree. Hashing everything would make
        the app read stale on every note and doc edit."""
        before, after = self._changed("README.md", "a new paragraph\n")
        self.assertEqual(before, after)


# --------------------------------------------------------------------------
# The nightly's own lock.
# --------------------------------------------------------------------------

class TheNightlyLockIsHeldBeforeItIsNamed(unittest.TestCase):
    """`mkdir` is atomic; the pid file that follows it is not.

    A second run arriving in the sliver between the two found a lock directory
    with nothing inside, read the holder as empty, called it stale, deleted the
    FIRST run's lock and took its own. Both nightlies then ran together, which
    is what the lock exists to stop, and the first run's EXIT trap later removed
    the second's lock.

    The refusal path costs nothing to test. The CLEARING path runs the whole
    script, so every python step is stubbed to fail instantly and HOME is
    redirected — no Amazon, no real catalogue folder, no notification.

    (The live-pid case lives in tests/fail_closed_tests.py, which owned this
    lock before the pid-less window was found.)
    """

    SCRIPT = os.path.join(HERE, "run_scheduled.sh")

    def _run(self, make_lock):
        data = tempfile.mkdtemp(prefix="nightlylock-")
        os.makedirs(os.path.join(data, "outputs"))
        lock = os.path.join(data, "outputs", "run_scheduled.lock")
        make_lock(lock)

        d = tempfile.mkdtemp(prefix="nightlystub-")
        # Nothing may reach a real interpreter: every step must die instantly.
        _write(os.path.join(d, "python3"), "#!/bin/bash\nexit 1\n", True)
        for name in ("osascript", "terminal-notifier"):
            _write(os.path.join(d, name), "#!/bin/bash\nexit 0\n", True)
        env = dict(os.environ)
        env["PATH"] = d + os.pathsep + env.get("PATH", "")
        env["MERCHADS_DATA_DIR"] = data
        env["ADS_PYTHON"] = os.path.join(d, "python3")
        env["HOME"] = tempfile.mkdtemp(prefix="nightlyhome-")
        r = subprocess.run(["bash", self.SCRIPT], env=env, capture_output=True,
                           text=True, timeout=300)
        return r, lock

    def test_a_lock_with_no_pid_yet_is_not_stolen(self):
        r, lock = self._run(lambda p: os.makedirs(p))
        self.assertEqual(75, r.returncode,
                         f"a second run started anyway\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
        self.assertIn("has not written its pid yet", r.stderr)
        self.assertTrue(os.path.isdir(lock),
                        "the first run's lock was deleted by the second run")

    def test_an_abandoned_lock_with_no_pid_is_still_cleared(self):
        """The grace period must not become a refusal that never lifts."""
        def old_and_empty(p):
            os.makedirs(p)
            os.utime(p, (1, 1))          # 1970: far past any grace period
        r, _lock = self._run(old_and_empty)
        self.assertNotEqual(75, r.returncode,
                            f"an abandoned lock blocked the run forever\n{r.stderr[-800:]}")
        self.assertIn("clearing a stale lock", r.stderr)

    def test_a_dead_pid_is_cleared_even_when_the_lock_is_new(self):
        """The grace period is about a MISSING pid, not about a young lock. A
        run that crashed a second after taking its lock must not hold it."""
        dead = subprocess.Popen(["/usr/bin/true"])
        dead.wait()

        def fresh_with_a_dead_pid(p):
            os.makedirs(p)
            with open(os.path.join(p, "pid"), "w", encoding="utf-8") as f:
                f.write(str(dead.pid))
        r, _lock = self._run(fresh_with_a_dead_pid)
        self.assertNotEqual(75, r.returncode,
                            f"a dead holder blocked the run\n{r.stderr[-800:]}")
        self.assertIn("clearing a stale lock", r.stderr)


if __name__ == "__main__":
    unittest.main()
