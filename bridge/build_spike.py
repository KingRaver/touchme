"""Build the Phase 0 bridge .tox from bridge/src/callbacks.py using TouchDesigner's toecollapse.

Output goes to bridge/build/ (gitignored: it embeds a fresh per-build token).

Usage:
  python3 bridge/build_spike.py [--port 9991] [--test-fixtures]

  --test-fixtures   also compile in GET /ensure-demo, which the integration test needs.
                    Default builds are read-only.

Environment:
  TD_APP         TouchDesigner.app path (default /Applications/TouchDesigner.app)

Everything is written with mode 0600/0700, because the token is embedded in the callbacks
text. The token is also written to bridge/build/token.txt. Export it for the server:
  export TD_MCP_TOKEN_FILE=$PWD/bridge/build/token.txt

The build happens in a temporary directory and replaces bridge/build/ only on success, so a
failed run keeps the previous .tox and token.
"""

import argparse
import os
import platform
import plistlib
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "build")
NAME = "td_mcp_bridge"
FALLBACK_TD_BUILD = "2025.33230"  # the build this was developed on


def encode_dat_text(text: bytes) -> bytes:
	# DAT text file layout, verified byte-for-byte in the Phase 0 spike.
	return b"2\n*" + struct.pack(">5I", 1, 1, 1, 1, 2) + struct.pack(">I", len(text)) + text


def write(path, data):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "wb" if isinstance(data, bytes) else "w") as f:
		f.write(data)


def td_build(app_path):
	try:
		with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
			return str(plistlib.load(f)["CFBundleShortVersionString"])
	except Exception:  # noqa: BLE001 - any unreadable or malformed plist falls back
		return FALLBACK_TD_BUILD


def prepare_script(token, test_fixtures):
	"""Return bridge/src/callbacks.py with the build-time placeholders substituted.

	Always reads the source fresh from disk, so what gets embedded in the .tox can never
	be a stale cached copy: the prior-art bug this guards against is a built .tox that
	embeds a script older than its own source (research/PRIOR_ART_TOUCHDESIGNER_MCP.md,
	finding 3). Covered without TouchDesigner by tests/unit/test_build_spike.py.
	"""
	with open(os.path.join(HERE, "src", "callbacks.py"), encoding="utf-8") as f:
		source = f.read()
	return source.replace("__TOKEN__", token).replace("__TEST_FIXTURES__", "1" if test_fixtures else "0")


def build(tmp, port, test_fixtures, app_path):
	toecollapse = os.path.join(app_path, "Contents", "MacOS", "toecollapse")
	if not os.access(toecollapse, os.X_OK):
		sys.exit(f"toecollapse not found at {toecollapse}. Set TD_APP to your TouchDesigner.app.")

	token = secrets.token_urlsafe(24)
	script = prepare_script(token, test_fixtures)

	d = os.path.join(tmp, NAME + ".tox.dir")
	child = os.path.join(d, NAME)
	stamp = time.strftime("%a %b %d %H:%M:%S %Y")
	osversion = platform.mac_ver()[0] or "unknown"
	write(
		os.path.join(d, ".build"),
		f"version 099\nbuild {td_build(app_path)}\ntime {stamp}\nosname macOS\nosversion {osversion}\n",
	)
	write(os.path.join(d, NAME + ".n"), "COMP:base\nv 592.016 1341.81 0.321455\ntile 200 250 160 130\nflags =  picked on current on viewer 1 parlanguage 0\ncolor 0.55 0.55 0.55 \nend\n")
	write(os.path.join(d, NAME + ".parm"), "?\npageindex 0 3\nenableexternaltox 0 off\n?\n")
	write(os.path.join(child, "ws.n"), "DAT:webserver\ntile 850 775 369 152\nflags =  viewer 1 parlanguage 0\ncolor 0.55 0.55 0.55 \nview -1 8 0 1 1 1 0 0 0 0 1 1 0\nend\n")
	write(os.path.join(child, "ws.parm"), f"?\nactive 0 on\nport 0 {port}\nlocaladdress 0 127.0.0.1\ncallbacks 32 cb\n?\n")
	write(os.path.join(child, "cb.n"), "DAT:text\ntile 825 300 644 380\nflags =  viewer 1 parlanguage 0\ncolor 0.55 0.55 0.55 \ndock ws\nview -1 8 0 1 1 1 0 -20.4274 0 0 1 1 0\nend\n")
	write(os.path.join(child, "cb.parm"), "?\nsyncfile 0 on\nlanguage 0 python\n?\n")
	write(os.path.join(child, "cb.text"), encode_dat_text(script.encode("utf-8")))

	toc = ["# 4 0 0 0 1", ".build", NAME + ".n", NAME + ".parm",
	       NAME + "/ws.n", NAME + "/ws.parm", NAME + "/cb.n", NAME + "/cb.parm", NAME + "/cb.text"]
	write(os.path.join(tmp, NAME + ".tox.toc"), "\n".join(toc) + "\n")
	tox = os.path.join(tmp, NAME + ".tox")
	subprocess.run([toecollapse, tox], check=True)
	if not os.path.isfile(tox) or os.path.getsize(tox) == 0:
		raise RuntimeError("toecollapse exited 0 but wrote no .tox; keeping the previous build")
	write(os.path.join(tmp, "token.txt"), token + "\n")


def port_number(text):
	port = int(text)
	if not 1024 <= port <= 65535:
		raise argparse.ArgumentTypeError("port must be 1024-65535")
	return port


def main():
	parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
	parser.add_argument("--port", type=port_number, default=9991)
	parser.add_argument("--test-fixtures", action="store_true")
	args = parser.parse_args()
	app_path = os.environ.get("TD_APP", "/Applications/TouchDesigner.app")

	os.umask(0o077)  # everything below, and toecollapse's output, is private to this user
	tmp = tempfile.mkdtemp(prefix=".build-", dir=HERE)
	old = tmp + ".old"
	try:
		build(tmp, args.port, args.test_fixtures, app_path)
		if os.path.exists(OUT):
			os.rename(OUT, old)
		os.rename(tmp, OUT)
	except BaseException:
		if os.path.exists(old) and not os.path.exists(OUT):
			os.rename(old, OUT)  # put the previous build back
		shutil.rmtree(tmp, ignore_errors=True)
		raise
	# The previous build holds the previous token. Say so if it cannot be removed.
	shutil.rmtree(old, ignore_errors=True)
	if os.path.exists(old):
		print(f"warning: could not remove {old} (holds the previous token). Delete it.", file=sys.stderr)
	print("built:", os.path.join(OUT, NAME + ".tox"))
	print(f"port: {args.port} | test fixtures: {args.test_fixtures} | token file (0600): {os.path.join(OUT, 'token.txt')}")


if __name__ == "__main__":
	main()
