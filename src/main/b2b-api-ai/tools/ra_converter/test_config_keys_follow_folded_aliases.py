"""A placeholder the template carries is a key the runtime pulls.

`_fold_placeholder_aliases` rewrites `#c_id#` to `#client_id#` in the
written request body. The classifier that fills TestSupport.CONFIG_KEYS
read the body BEFORE the fold, so it listed `c_id` only. At run time the
merged row held `c_id`, the body asked for `client_id`, and the token
request went out as

    {"client_id": null, "client_secret": null, ...}
    HTTP 401 A valid OAuth client could not be found for client_id: null

Every test in the suite then failed on its first authenticated step.
Twelve suites carried the mismatch; the ones that passed did so only
because some other case happened to spell the placeholder `#client_id#`.

    python tools/ra_converter/test_config_keys_follow_folded_aliases.py
"""
import glob
import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

AMEX = os.path.join(HERE, "input", "amexbackbook.xml")
DATA_DIR = os.environ.get("RA_TEST_DATA_DIR", "")


def _aliases():
    return {a: c for a, c in
            (rc.converter_config().get("placeholder_aliases") or {}).items()
            if not a.startswith("_")}


def test_there_are_aliases_to_guard():
    assert _aliases(), "no placeholder_aliases configured: nothing is checked"


def test_a_folded_alias_brings_its_canonical_key():
    for alias, canonical in _aliases().items():
        keys = rc._config_keys_with_folded_aliases({"base_url", alias})
        assert alias in keys and canonical in keys, (alias, canonical, keys)


def test_keys_without_an_alias_are_left_alone():
    keys = {"base_url", "username", "password"}
    assert rc._config_keys_with_folded_aliases(keys) == keys


def test_the_canonical_key_alone_adds_nothing():
    for canonical in set(_aliases().values()):
        assert rc._config_keys_with_folded_aliases({canonical}) == {canonical}


def test_every_key_the_fold_writes_is_in_the_result():
    """The property itself: fold a body, then every alias-derived
    placeholder left in it must be a config key."""
    for alias, canonical in _aliases().items():
        body = rc._fold_placeholder_aliases('{"x": "#%s#"}' % alias)
        assert "#%s#" % canonical in body
        keys = rc._config_keys_with_folded_aliases({alias})
        for ph in re.findall(r"#([A-Za-z0-9_]+)#", body):
            assert ph in keys, (ph, keys)


def test_a_real_convert_lists_the_keys_its_token_body_uses():
    if not os.path.isfile(AMEX):
        print("    (skipped: amexbackbook.xml not in input/)")
        return
    out = tempfile.mkdtemp(prefix="amexcfg")
    argv = [sys.executable, os.path.join(HERE, "ra_converter.py"),
            "--input", AMEX, "--output", out,
            "--skip-self-test", "--no-cursor-assist"]
    if os.path.isdir(DATA_DIR):
        argv += ["--data-dir", DATA_DIR]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=out)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]

    support = glob.glob(os.path.join(out, "src", "main", "java", "**",
                                     "amexbackbook", "TestSupport.java"),
                        recursive=True)
    assert support, "no TestSupport.java generated"
    src = io.open(support[0], encoding="utf-8").read()
    m = re.search(r"CONFIG_KEYS = new String\[\] \{([^}]*)\}", src)
    assert m, "CONFIG_KEYS not found"
    keys = set(re.findall(r'"([^"]+)"', m.group(1)))

    bodies = glob.glob(os.path.join(out, "src", "main", "resources", "templates",
                                    "amexbackbook", "**", "tokenrequest*.json"),
                       recursive=True)
    assert bodies, "no token request template generated"
    wanted = set()
    for b in bodies:
        wanted.update(re.findall(r"#([A-Za-z0-9_]+)#",
                                 io.open(b, encoding="utf-8").read()))
    assert {"client_id", "client_secret"} <= wanted, (
        "the token body no longer carries the placeholders this guards", wanted)
    missing = sorted(wanted - keys)
    assert not missing, (
        "token body placeholders the runtime never pulls from config: %s "
        "(CONFIG_KEYS=%s)" % (missing, sorted(keys)))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
