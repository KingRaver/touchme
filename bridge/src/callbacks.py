"""TouchDesigner bridge: perception (read-only) and construction (mutating) routes.

Web Server DAT callbacks. The DAT binds to 127.0.0.1 (set by bridge/build_spike.py).
Every request needs a loopback Host, a loopback-or-absent Origin, and
`Authorization: Bearer <token>`. Callbacks run on the main thread
(research/PHASE0_SPIKE_RESULTS.md), so operators are touched directly.

Read routes: GET /ping, GET /capture?path=<absolute TOP path>&format=png|jpg,
GET /project-info, GET /errors?path=<absolute path>,
GET /snapshot?path=<absolute path>&maxdepth=<int>,
GET /children?path=<absolute path>&family=<str>&type=<str>&offset=<int>&limit=<int>,
GET /operator?path=<absolute path>, GET /parameters?path=<absolute path>&name=<pattern>,
GET /describe-type?type=<str>&name=<pattern>&page=<str>&offset=<int>&limit=<int>,
GET /performance?path=<absolute path>&top_n=<int>&maxdepth=<int>,
GET /chop?path=<absolute CHOP path>&channels=<pattern>&start=<int>&count=<int>,
GET /dat?path=<absolute DAT path>&row_offset=<int>&row_limit=<int>,
GET /sop-info?path=<absolute SOP path>,
GET /contact-cells?paths=<comma-separated paths>&frames=<comma-separated numbers>&format=png|jpg.
/describe-type reads a type's parameters from a temporary operator that it creates with cooking
off inside the bridge's own component and destroys before replying, outside any undo block.
Hardware types (DEVICE_TYPE_PREFIXES) are never created: creating some of them asked for camera
permission or opened network sockets (research/PHASE3_KNOWLEDGE_RESULTS.md).
/performance reads from a temporary Perform CHOP, created, cooked and destroyed the same way,
also outside any undo block. /contact-cells, when `frames` is given, moves the project timeline
to each requested frame and restores it before replying; nothing is left changed either way.
Change routes (POST, JSON object body): /create, /connect, /disconnect, /set-parameters,
/delete, /layout, /undo, /redo, /timeline. Each change is one TouchDesigner undo entry and is
written to the audit log before it is applied; if the log cannot be written, nothing is changed.
The bridge's own component and TouchDesigner's /ui and /sys networks cannot be changed through
these routes. That guards against mistakes only: a parameter expression is Python run by
TouchDesigner, so any caller that can set one can reach anything (decisions/0003).

Test builds only (build_spike.py --test-fixtures): GET /ensure-demo, which creates a demo TOP
inside the bridge's own container, and GET /foreign-edit, which adds an undo entry this server
did not make, standing in for a hand edit.

TouchDesigner re-executes this module on every request (research/PHASE2_UNDO_RESULTS.md), so
nothing may be kept in module globals between requests.
"""

import base64
import contextlib
import difflib
import fnmatch
import hmac
import json
import math
import os
import re
import secrets
import time
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	# TouchDesigner injects these at runtime. Declared for type checkers only; never executed.
	from typing import Any

	def op(path: str) -> Any: ...
	def parent() -> Any: ...

	app: Any = None
	project: Any = None
	constantTOP: Any = None
	families: Any = None
	ui: Any = None
	ParMode: Any = None

TOKEN = "__TOKEN__"
_FIXTURES_FLAG = "__TEST_FIXTURES__"  # "1" or "0", set by build_spike.py
ENABLE_TEST_FIXTURES = _FIXTURES_FLAG == "1"
# 0.3.0 added the POST change routes, 0.4.0 added /describe-type, and 0.5.0 added /performance,
# /chop, /dat, /sop-info, /contact-cells and /timeline. A server that requires the newer version
# refuses an older bridge with a rebuild message instead of a "no route" error.
API_VERSION = "0.5.0"
# Built from pieces so build_spike.py's replace of the token placeholder cannot rewrite it.
_TOKEN_PLACEHOLDER = "__TOK" + "EN__"
_AUDIT_SETTING = "__AUDIT_LOG__"  # a file path, set by build_spike.py
_AUDIT_PLACEHOLDER = "__AUDIT" + "_LOG__"
AUDIT_LOG = (
	os.path.join(os.path.expanduser("~"), ".td-mcp", "audit.jsonl")
	if _AUDIT_SETTING == _AUDIT_PLACEHOLDER
	else _AUDIT_SETTING
)
# Refuse before encoding: encoding a huge TOP blocks TouchDesigner's main thread.
MAX_PIXELS = 2048 * 2048
# PNG of a detailed TOP is slow (about 140 ms at 1280x720 in Phase 0) and large; keep it smaller.
MAX_PIXELS_PNG = 1280 * 1280
# Raw image bytes. Base64 adds a third, and model APIs reject images over about 5 MB.
# This is checked after the encode, so refusing an image still costs one full encode on the
# main thread; the pixel limits above bound that. Phase 1's downscale removes it.
MAX_BYTES = 3 * 1024 * 1024
# /snapshot walks the tree and serialises it on the main thread: bound both axes.
MAX_SNAPSHOT_DEPTH = 8
MAX_SNAPSHOT_OPERATORS = 1000
# /children page size ceiling.
MAX_CHILDREN_LIMIT = 500
# TouchDesigner's own interface and system networks. A project-wide ("/") /errors or
# /snapshot reply leaves them out and says so in `excluded`; either path can be asked for
# directly. Why: research/PHASE1_LIVE_RESULTS.md.
INTERNAL_ROOTS = ("/ui", "/sys")
# /local holds the project's timeline and shared settings: editable, but not deletable here.
UNDELETABLE_ROOTS = ("/local",)
# Every undo entry this bridge makes starts with this; /undo and /redo touch no other entry.
UNDO_PREFIX = "td-mcp "
# COMP storage key (module globals do not survive a request) for changes that failed and were
# rolled back. /redo refuses them, so a half-applied change cannot be replayed.
ROLLED_BACK_KEY = "td_mcp_rolled_back"
MAX_ROLLED_BACK = 50
MAX_BODY_BYTES = 256 * 1024
MAX_PARAMETER_CHANGES = 100
MAX_UNDO_STEPS = 20
MAX_LAYOUT_OPERATORS = 1000
MAX_AUDIT_STRING = 2000
LAYOUT_GAP_X = 80
LAYOUT_GAP_Y = 50
# /describe-type: the temporary container, page sizes, and text limits that protect context.
SCRATCH_NAME = "td_mcp_describe"
DEFAULT_TYPE_PARS = 60
MAX_TYPE_PARS = 250
MAX_BRIEF_HELP = 200
MAX_HELP = 2000
# Types that talk to capture, audio, sensor, laser, lighting, serial or input hardware, matched
# as prefixes of the type name without its family ("videodevice" covers videodeviceinTOP and
# videodevicesDAT). /describe-type never creates them. Only some were seen to have side effects;
# the rest are listed because a side effect need not be observable on the machine that probed.
DEVICE_TYPE_PREFIXES = (
	"audiodevice", "blacktrax", "directdisplayout", "dmxin", "dmxout", "etherdream", "freed",
	"hokuyo", "joystick", "keyboardin", "kinect", "laserdevice", "leapmotion", "leuze", "midi",
	"mosys", "mousein", "mouseout", "multitouchin", "ncam", "nuitrack", "oak", "oculus", "openvr",
	"optitrack", "orbbec", "ouster", "pangolin", "pantilt", "realsense", "screengrab", "serial",
	"sick", "st2110", "stype", "tablet", "videodevice", "zed",
)
PARAMETER_HELP_FILE = "TDParameterHelp.json"
_NO_HELP = "Help Not Available."
_NAME_RULE = re.compile(r"[A-Za-z0-9_]+")
_REDACTED = "<redacted>"
# /performance: the temporary Perform CHOP's own name, each toggle parameter it turns on with the
# channel that toggle outputs (the names differ; read live on 2025.33230,
# research/PHASE4_LIVE_RESULTS.md; temperature and battery channels are left out), and the
# ranking's page size.
PERF_SCRATCH_NAME = "td_mcp_perf"
PERF_CHANNELS = {
	"fps": "fps",
	"msec": "msec",
	"droppedframes": "dropped_frames",
	"gpumemused": "gpu_mem_used",
	"totalgpumem": "total_gpu_mem",
	"cpumemused": "cpu_mem_used",
	"activeops": "active_ops",
	"deactivatedops": "deactivated_ops",
	"totalops": "total_ops",
	"cookrate": "cookrate",
	"cookrealtime": "cookrealtime",
}
DEFAULT_PERF_TOP_N = 20
MAX_PERF_TOP_N = 200
# /chop: sample-range and channel-count limits to protect context.
DEFAULT_CHOP_SAMPLES = 100
MAX_CHOP_SAMPLES = 2000
MAX_CHOP_CHANNELS = 64
MAX_CHOP_VALUES = 8000  # across all channels of one reply
# /dat: row/column/text limits to protect context.
DEFAULT_DAT_ROWS = 100
MAX_DAT_ROWS = 500
MAX_DAT_COLS = 64
MAX_DAT_CELL_CHARS = 500
MAX_DAT_TEXT_CHARS = 20000
MAX_DAT_REPLY_CHARS = 40000  # across all cells of one reply; at least one row is returned
# /contact-cells: a cell larger than MAX_CONTACT_CELL_PIXELS is scaled down, since a sheet
# multiplies one capture's cost by its cell count.
MAX_CONTACT_PATHS = 16
MAX_CONTACT_FRAMES = 12
MAX_CONTACT_CELLS = 48
MAX_CONTACT_CELL_PIXELS = 480 * 480
MAX_CONTACT_CELL_BYTES = 768 * 1024
MAX_CONTACT_TOTAL_BYTES = 10 * 1024 * 1024


def _header(request, name):
	for key, value in request.items():
		if isinstance(key, str) and key.lower() == name:
			return value
	return None


def _loopback_netloc(value):
	"""True if `value` names 127.0.0.1, as either a bare Host header or a scheme:// Origin."""
	if not isinstance(value, str) or not value or "@" in value:
		return False  # "@" is userinfo: "127.0.0.1:1@evil.com" names evil.com, not loopback
	netloc = value.split("://", 1)[1] if "://" in value else value
	netloc = netloc.split("/", 1)[0]
	host = netloc.split(":", 1)[0]
	return host == "127.0.0.1"


def _resolve_exact(path):
	"""Return the operator at exactly `path`, or None.

	`op()` accepts globs and returns an arbitrary match, so a returned operator is only
	trusted when its own path equals the requested path.
	"""
	if not isinstance(path, str) or not path.startswith("/"):
		return None
	found = op(path)
	if found is None or found.path != path:
		return None
	return found


def _ensure_demo():
	"""Create a 640x360 orange constant TOP inside the bridge's own container, once."""
	box = parent()
	demo = box.op("demo")
	if demo is None:
		demo = box.create(constantTOP, "demo")
		demo.par.outputresolution = "custom"
		demo.par.resolutionw = 640
		demo.par.resolutionh = 360
		demo.par.colorr = 0.9
		demo.par.colorg = 0.2
		demo.par.colorb = 0.1
	return demo


def _foreign_edit_route(pars):
	ui.undo.startBlock("Test Foreign Edit")
	try:
		demo = _ensure_demo()
		demo.nodeX += 10
	finally:
		ui.undo.endBlock()
	return {"undo_label": "Test Foreign Edit"}


def _ping(pars):
	return {
		"api_version": API_VERSION,
		"td_build": str(app.build),
		"td_version": str(app.version),
		"bridge_path": parent().path,
	}


def _ensure_demo_route(pars):
	return {"demo_top": _ensure_demo().path}


def _capture(pars):
	path = pars.get("path")
	fmt = pars.get("format", "png")
	if fmt not in ("png", "jpg"):
		return {"error": "format must be png or jpg"}
	target = _resolve_exact(path)
	if target is None:
		return {"error": "no operator at exactly %r. Use an absolute path, no wildcards." % (path,)}
	if target.family != "TOP":
		return {"error": "%s is a %s, not a TOP." % (path, target.family)}
	limit = MAX_PIXELS_PNG if fmt == "png" else MAX_PIXELS
	if target.width * target.height > limit:
		return {
			"error": "%s is %dx%d, over the %d-pixel limit for %s. Downscaling is not built yet "
			"(Phase 1); capture a smaller TOP%s." % (
				path, target.width, target.height, limit, fmt, ", or use jpg" if fmt == "png" else ""
			)
		}
	data = bytes(target.saveByteArray("." + fmt))
	if len(data) > MAX_BYTES:
		return {
			"error": "image is %d bytes, over the %d-byte limit. Downscaling is not built yet "
			"(Phase 1); capture a smaller TOP, or use jpg." % (len(data), MAX_BYTES)
		}
	return {
		"format": fmt,
		"width": target.width,
		"height": target.height,
		"data_b64": base64.b64encode(data).decode("ascii"),
	}


def _bad_path(path):
	return {"error": "no operator at exactly %r. Use an absolute path, no wildcards." % (path,)}


def _excluded_roots(path):
	return list(INTERNAL_ROOTS) if path == "/" else []


def _is_under(path, roots):
	return any(path == root or path.startswith(root + "/") for root in roots)


def _flags(target):
	return {
		"bypass": bool(getattr(target, "bypass", False)),
		"lock": bool(getattr(target, "lock", False)),
		"selected": bool(getattr(target, "selected", False)),
		"display": bool(getattr(target, "display", False)),
		"render": bool(getattr(target, "render", False)),
	}


def _io_paths(ops):
	return [o.path for o in (ops or []) if o is not None]


def _jsonable(value):
	"""A Par's evaluated value is a number/string/bool, or an OP for an OP-type parameter.
	A number that is not finite becomes "nan", "inf" or "-inf": JSON has no such numbers."""
	if isinstance(value, float) and not math.isfinite(value):
		return str(value)
	if value is None or isinstance(value, (int, float, str, bool)):
		return value
	path = getattr(value, "path", None)
	return path if isinstance(path, str) else str(value)


def _all_pars(target):
	return list(getattr(target, "builtinPars", None) or []) + list(getattr(target, "customPars", None) or [])


def _clamp_limit(par, limit_name, clamp_name):
	"""Return par.min or par.max only when TouchDesigner clamps to it, else None.

	An unclamped limit is not a range: values may lie outside it (docs.derivative.ca/Par_Class,
	and research/PHASE1_LIVE_RESULTS.md).
	"""
	try:
		limit = getattr(par, limit_name) if getattr(par, clamp_name) else None
	except Exception:
		return None
	return None if isinstance(limit, float) and not math.isfinite(limit) else limit


def _par_detail(par):
	readable = True
	try:
		value = _jsonable(par.eval())
	except Exception:
		value, readable = None, False
	try:
		is_default = bool(par.isDefault)
	except Exception:
		is_default, readable = None, False
	mode = getattr(par, "mode", None)
	menu_names = getattr(par, "menuNames", None)
	menu_labels = getattr(par, "menuLabels", None)
	return {
		"name": par.name,
		"value": value,
		"expr": getattr(par, "expr", None),
		"mode": str(mode) if mode is not None else None,
		"is_default": is_default,
		"readable": readable,
		"min": _clamp_limit(par, "min", "clampMin"),
		"max": _clamp_limit(par, "max", "clampMax"),
		"menu_names": list(menu_names) if menu_names else None,
		"menu_labels": list(menu_labels) if menu_labels else None,
	}


_SEVERITIES = ("Error:", "Warning:")


def _strip_severity(text):
	for prefix in _SEVERITIES:
		if text.startswith(prefix):
			return text[len(prefix):].lstrip()
	return text


def _parse_stream(text, root_path):
	"""Split an errors()/warnings() string into {"path", "message"} rows, one per message.

	Verified line formats (research/PHASE0_SPIKE_RESULTS.md): errors() is
	"<path>:  Error: <msg>" (two spaces), warnings() is "<path>:Warning: <msg>" (none).
	Further warnings from the same operator follow as bare "Warning: <msg>" lines
	(research/PHASE1_LIVE_RESULTS.md); each is its own row under that operator. Bare "Error:"
	lines are treated the same way. Any other line (a wrapped message, a traceback) continues
	the previous row.
	"""
	rows = []
	for line in (text or "").splitlines():
		line = line.strip()
		if not line:
			continue
		owner, sep, rest = line.partition(":")
		if sep and owner.startswith("/") and not any(c.isspace() for c in owner):
			rows.append({"path": owner, "message": _strip_severity(rest.lstrip())})
		elif not rows:
			rows.append({"path": root_path, "message": line})
		elif line.startswith(_SEVERITIES):
			rows.append({"path": rows[-1]["path"], "message": _strip_severity(line)})
		else:
			rows[-1]["message"] += "\n" + line
	return rows


def _project_info(pars):
	root = op("/")
	clock = getattr(root, "time", None) if root is not None else None
	timeline = None
	if clock is not None:
		timeline = {
			"frame": getattr(clock, "frame", None),
			"rate": getattr(clock, "rate", None),
			"start": getattr(clock, "start", None),
			"end": getattr(clock, "end", None),
			"play": bool(clock.play) if hasattr(clock, "play") else None,
		}
	return {
		"api_version": API_VERSION,
		"td_build": str(app.build),
		"td_version": str(app.version),
		"project_name": str(project.name),
		"project_folder": str(project.folder),
		"cook_rate": project.cookRate,
		"timeline": timeline,
	}


def _errors(pars):
	path = pars.get("path", "/")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	unread = []
	errors_text = warnings_text = None
	try:
		errors_text = target.errors(recurse=True)
	except Exception:
		unread.append("errors")
	try:
		warnings_text = target.warnings(recurse=True)
	except Exception:
		unread.append("warnings")
	excluded = _excluded_roots(path)

	def rows(text):
		parsed = _parse_stream(text, path) if text is not None else []
		return [row for row in parsed if not _is_under(row["path"], excluded)]

	errors = rows(errors_text)
	warnings = rows(warnings_text)
	if unread:
		status = "incomplete"
	elif errors or warnings:
		status = "has_issues"
	else:
		status = "clean"
	return {
		"path": path,
		"status": status,
		"unread_streams": unread,
		"errors": errors,
		"warnings": warnings,
		"excluded": excluded,
		"caveats": [
			(
				"An exception raised inside a Script operator's own callback (e.g. onCook) does "
				"not reach these streams unless the callback catches it and calls addError(); a "
				"'clean' status does not rule that out."
			)
		],
	}


def _snapshot(pars):
	path = pars.get("path", "/")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	try:
		depth = int(pars.get("maxdepth", 1))
	except (TypeError, ValueError):
		return {"error": "maxdepth must be an integer"}
	if depth < 0:
		return {"error": "maxdepth must be >= 0"}
	depth = min(depth, MAX_SNAPSHOT_DEPTH)
	descendants = target.findChildren(maxDepth=depth) if hasattr(target, "findChildren") else []
	excluded = _excluded_roots(path)
	# Filtered before the cap, so excluded operators never use up the operator budget.
	nodes = [target] + [n for n in descendants if not _is_under(n.path, excluded)]
	truncated = len(nodes) > MAX_SNAPSHOT_OPERATORS
	nodes = nodes[:MAX_SNAPSHOT_OPERATORS]
	operators = [
		{
			"path": n.path,
			"type": getattr(n, "type", None),
			"family": getattr(n, "family", None),
			"flags": _flags(n),
			"inputs": _io_paths(getattr(n, "inputs", None)),
			"outputs": _io_paths(getattr(n, "outputs", None)),
		}
		for n in nodes
	]
	return {
		"path": path,
		"max_depth": depth,
		"operator_count": len(operators),
		"truncated": truncated,
		"excluded": excluded,
		"operators": operators,
	}


def _children(pars):
	path = pars.get("path", "/")
	family = pars.get("family")
	optype = pars.get("type")
	try:
		offset = int(pars.get("offset", 0))
		limit = int(pars.get("limit", 100))
	except (TypeError, ValueError):
		return {"error": "offset and limit must be integers"}
	if offset < 0 or limit <= 0:
		return {"error": "offset must be >= 0 and limit must be > 0"}
	limit = min(limit, MAX_CHILDREN_LIMIT)
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	kids = list(getattr(target, "children", None) or [])
	if family:
		kids = [k for k in kids if getattr(k, "family", None) == family]
	if optype:
		kids = [k for k in kids if getattr(k, "type", None) == optype]
	page = kids[offset:offset + limit]
	end = offset + limit
	return {
		"path": path,
		"total": len(kids),
		"offset": offset,
		"limit": limit,
		"next_offset": end if end < len(kids) else None,
		"children": [
			{"path": k.path, "type": getattr(k, "type", None), "family": getattr(k, "family", None)}
			for k in page
		],
	}


def _operator(pars):
	path = pars.get("path")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	non_default = []
	for par in _all_pars(target):
		detail = _par_detail(par)
		# An unreadable parameter (is_default None) is listed with readable False, never dropped.
		if detail["is_default"] is not True:
			non_default.append(detail)
	return {
		"path": target.path,
		"type": getattr(target, "type", None),
		"family": getattr(target, "family", None),
		"flags": _flags(target),
		"inputs": _io_paths(getattr(target, "inputs", None)),
		"outputs": _io_paths(getattr(target, "outputs", None)),
		"non_default_parameters": non_default,
	}


def _parameters(pars):
	path = pars.get("path")
	name_pattern = pars.get("name")
	# pars() takes several patterns as separate arguments; one "tx ty" string matches nothing.
	patterns = (name_pattern or "").split()
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	all_pars = _all_pars(target)
	if patterns:
		try:
			allowed = {p.name for p in target.pars(*patterns)}
		except Exception:
			return {"error": "bad name pattern %r" % (name_pattern,)}
		all_pars = [p for p in all_pars if p.name in allowed]
	parameters = [_par_detail(p) for p in all_pars]
	return {"path": target.path, "parameter_count": len(parameters), "parameters": parameters}


def _clip_text(text, limit):
	text = " ".join(str(text).split())
	return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _family_of(optype):
	for family, group in families.items():
		if any(t.__name__ == optype for t in group):
			return family
	return None


def _is_device_type(optype, family):
	return optype[: -len(family)].startswith(DEVICE_TYPE_PREFIXES)


def _type_par(par, full):
	"""One parameter of a type: what a new operator has, without this instance's values."""
	row = {"name": par.name}

	def put(key, value):
		if value is not None and value != "" and value != []:
			row[key] = value

	def read(attribute):
		try:
			return getattr(par, attribute)
		except Exception:
			return None

	put("label", read("label"))
	style = read("style")
	put("style", style)
	put("page", getattr(read("page"), "name", None))
	if style not in ("Pulse", "Momentary", "Header", "Sequence"):
		default = read("default")
		if default is not None:
			row["default"] = _jsonable(default)  # kept even when it is an empty string
	if str(read("defaultMode")).rsplit(".", 1)[-1] == "EXPRESSION":
		put("default_expr", read("defaultExpr"))
	# min, max and the slider range mean something only on numbers, and min and max only when
	# TouchDesigner clamps to them (research/PHASE1_LIVE_RESULTS.md).
	if read("isNumber"):
		put("min", _clamp_limit(par, "min", "clampMin"))
		put("max", _clamp_limit(par, "max", "clampMax"))
		low, high = read("normMin"), read("normMax")
		if low is not None and high is not None and math.isfinite(low) and math.isfinite(high):
			row["slider_range"] = [low, high]
	# Menus, and strings that offer suggestions. Number presets and toggles are left out.
	if read("isMenu") or read("isString"):
		put("menu_names", list(read("menuNames") or []))
		if full:
			put("menu_labels", list(read("menuLabels") or []))
	text = read("help")
	if text and text != _NO_HELP:
		put("help", _clip_text(text, MAX_HELP if full else MAX_BRIEF_HELP))
	if read("readOnly"):
		row["read_only"] = True
	if read("isCustom"):
		row["custom"] = True
	put("sequence", getattr(read("sequence"), "name", None))
	return row


def _page_slice(rows, offset, limit):
	end = offset + limit
	return {
		"parameter_count": len(rows),
		"offset": offset,
		"limit": limit,
		"next_offset": end if end < len(rows) else None,
	}, rows[offset:end]


def _match_page(wanted, pages):
	for page in pages:
		if page.lower() == wanted.lower():
			return page
	return None


def _unknown_page(wanted, pages):
	return {"error": "no parameter page %r. Pages: %s." % (wanted, ", ".join(pages) or "none")}


def _from_scratch(optype, family, name_pattern, page, offset, limit):
	"""Describe `optype` from a temporary operator. Nothing is left in the project, and the
	undo history is untouched because no undo block is opened."""
	box = parent()
	stale = box.op(SCRATCH_NAME)
	if stale is not None:
		stale.destroy()  # left by a request that never finished
	scratch = box.create("baseCOMP", SCRATCH_NAME)
	try:
		# Set before anything is created inside, so the temporary operator never cooks.
		scratch.allowCooking = False
		try:
			made = scratch.create(optype)
		except Exception as exc:
			return {"error": "TouchDesigner could not create a %s to read it from: %s" % (optype, exc)}
		# Parameters without a page are internal (pageindex).
		pars = [p for p in _all_pars(made) if getattr(p, "page", None) is not None]
		pages = list(dict.fromkeys(p.page.name for p in pars))
		if name_pattern:
			try:
				allowed = {p.name for p in made.pars(*name_pattern.split())}
			except Exception:
				return {"error": "bad name pattern %r" % (name_pattern,)}
			pars = [p for p in pars if p.name in allowed]
		if page:
			found = _match_page(page, pages)
			if found is None:
				return _unknown_page(page, pages)
			pars = [p for p in pars if p.page.name == found]
		counts, shown = _page_slice(pars, offset, limit)
		return dict(
			counts,
			type=optype,
			family=family,
			label=getattr(made, "label", None),
			source="live",
			note=None,
			min_inputs=getattr(made, "minInputs", None),
			max_inputs=getattr(made, "maxInputs", None),
			pages=pages,
			also_creates=sorted(o.type + o.family for o in _all_children(scratch) if o.path != made.path),
			parameters=[_type_par(p, bool(name_pattern)) for p in shown],
		)
	finally:
		scratch.destroy()


def _help_entry(optype):
	"""This build's own parameter help for a type, or None. Raises OSError or ValueError."""
	with open(os.path.join(app.configFolder, PARAMETER_HELP_FILE), encoding="utf-8-sig") as handle:
		families_help = json.load(handle)["help"]
	for group in families_help.values():
		if optype in group:
			return group[optype]
	return None


def _from_help_file(optype, family, name_pattern, page, offset, limit):
	if page:
		return {"error": "pages are not known for %s, which is described from the help file" % optype}
	note = "%s talks to hardware, so no operator was created to read it. " % optype
	try:
		entry = _help_entry(optype)
	except (OSError, ValueError, KeyError, AttributeError) as exc:
		print("[td-mcp bridge] parameter help unreadable: %s" % exc)
		entry = None
	if entry is None:
		note += "This build's parameter help file has no entry for it."
		entry = {}
	else:
		note += (
			"This comes from the build's parameter help file, which has no defaults, pages or menu "
			"entries and names a vector such as tx/ty/tz once, as t."
		)
	note += (
		" For the full list, read an existing one with td_get_parameters, or ask the user before "
		"creating one with td_create_operator: that can ask for device permissions."
	)
	rows = []
	for name, detail in (entry.get("parameters") or {}).items():
		# Like TouchDesigner's own matching, a pattern may list several, separated by spaces.
		if name_pattern and not any(fnmatch.fnmatchcase(name, p) for p in name_pattern.split()):
			continue
		row = {"name": name}
		for key, source in (("label", "label"), ("style", "parType")):
			if detail.get(source):
				row[key] = detail[source]
		if detail.get("summary"):
			row["help"] = _clip_text(detail["summary"], MAX_HELP if name_pattern else MAX_BRIEF_HELP)
		rows.append(row)
	counts, shown = _page_slice(rows, offset, limit)
	return dict(
		counts,
		type=optype,
		family=family,
		label=entry.get("label"),
		source="help_file",
		note=note,
		min_inputs=None,
		max_inputs=None,
		pages=[],
		also_creates=[],
		parameters=shown,
	)


def _describe_type(pars):
	optype = pars.get("type")
	if not isinstance(optype, str) or not optype:
		return {"error": "type must be a non-empty string"}
	try:
		offset = int(pars.get("offset", 0))
		limit = int(pars.get("limit", DEFAULT_TYPE_PARS))
	except (TypeError, ValueError):
		return {"error": "offset and limit must be integers"}
	if offset < 0 or limit <= 0:
		return {"error": "offset must be >= 0 and limit must be > 0"}
	limit = min(limit, MAX_TYPE_PARS)
	family = _family_of(optype)
	if family is None:
		return {"error": _unknown_type(optype, _operator_types())}
	describe = _from_help_file if _is_device_type(optype, family) else _from_scratch
	name = " ".join(str(pars.get("name") or "").split()) or None  # blank means no filter
	return describe(optype, family, name, pars.get("page"), offset, limit)


def _finite(value):
	"""`value`, or None when it is missing or not a finite number (JSON has no NaN)."""
	return value if value is not None and math.isfinite(value) else None


def _perform_values():
	"""Create a temporary Perform CHOP inside the bridge's own component, turn on
	PERF_CHANNELS, force it to cook, read those channels, and destroy it. Returns the values
	by toggle name, and the channel names that were not there to read. Nothing is left in the
	project; like /describe-type's scratch operator, this runs outside any undo block."""
	box = parent()
	stale = box.op(PERF_SCRATCH_NAME)
	if stale is not None:
		stale.destroy()  # left by a request that never finished
	perf = box.create("performCHOP", PERF_SCRATCH_NAME)
	try:
		for name in PERF_CHANNELS:
			par = getattr(perf.par, name, None)
			if par is not None:
				par.val = True
		perf.cook(force=True)
		values, unread = {}, []
		for name, channel in PERF_CHANNELS.items():
			chan = perf.chan(channel)
			values[name] = None if chan is None else _finite(chan.eval())
			if values[name] is None:
				unread.append(channel)
		return values, unread
	finally:
		perf.destroy()


def _operator_cost(target):
	return {
		"path": target.path,
		"type": getattr(target, "type", None),
		"family": getattr(target, "family", None),
		"cpu_cook_ms": getattr(target, "cpuCookTime", None),
		"gpu_cook_ms": getattr(target, "gpuCookTime", None),
		"total_cooks": getattr(target, "totalCooks", None),
		"cooked_this_frame": getattr(target, "cookedThisFrame", None),
	}


def _performance(pars):
	path = pars.get("path", "/")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	try:
		top_n = int(pars.get("top_n", DEFAULT_PERF_TOP_N))
		depth = int(pars.get("maxdepth", MAX_SNAPSHOT_DEPTH))
	except (TypeError, ValueError):
		return {"error": "top_n and maxdepth must be integers"}
	if top_n <= 0:
		return {"error": "top_n must be > 0"}
	top_n = min(top_n, MAX_PERF_TOP_N)
	if depth < 0:
		return {"error": "maxdepth must be >= 0"}
	depth = min(depth, MAX_SNAPSHOT_DEPTH)
	try:
		values, unread = _perform_values()
	except Exception as exc:
		return {"error": "could not read the Perform CHOP: %s: %s" % (type(exc).__name__, exc)}
	excluded = _excluded_roots(path)
	descendants = target.findChildren(maxDepth=depth) if hasattr(target, "findChildren") else []
	nodes = [target] + [n for n in descendants if not _is_under(n.path, excluded)]
	truncated = len(nodes) > MAX_SNAPSHOT_OPERATORS
	nodes = nodes[:MAX_SNAPSHOT_OPERATORS]
	costed = sorted(
		(_operator_cost(n) for n in nodes), key=lambda row: row["cpu_cook_ms"] or 0.0, reverse=True
	)
	realtime = values.get("cookrealtime")
	return {
		"fps": values.get("fps"),
		"target_fps": values.get("cookrate"),
		"frame_ms": values.get("msec"),
		"dropped_frames": values.get("droppedframes"),
		"realtime": bool(realtime) if realtime is not None else None,
		"cpu_mem_mb": values.get("cpumemused"),
		"gpu_mem_mb": values.get("gpumemused"),
		"gpu_mem_total_mb": values.get("totalgpumem"),
		"active_ops": values.get("activeops"),
		"deactivated_op_calls": values.get("deactivatedops"),
		"total_ops": values.get("totalops"),
		"unread_channels": unread,
		"path": path,
		"max_depth": depth,
		"operator_count": len(nodes),
		"truncated": truncated,
		"excluded": excluded,
		"top_cpu_cost": costed[:top_n],
	}


def _sample(value):
	"""A sample as a number, or "nan", "inf" or "-inf": JSON has no such numbers, and they are
	what a caller debugging a CHOP most needs to see."""
	value = float(value)
	return value if math.isfinite(value) else str(value)


def _read_chop(pars):
	path = pars.get("path")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	if getattr(target, "family", None) != "CHOP":
		return {"error": "%s is a %s, not a CHOP." % (path, target.family)}
	name_pattern = pars.get("channels")
	try:
		start = int(pars.get("start", 0))
		count = int(pars.get("count", DEFAULT_CHOP_SAMPLES))
	except (TypeError, ValueError):
		return {"error": "start and count must be integers"}
	if start < 0 or count <= 0:
		return {"error": "start must be >= 0 and count must be > 0"}
	count = min(count, MAX_CHOP_SAMPLES)
	# chans() takes several patterns as separate arguments; one "tx ty" string matches nothing.
	patterns = (name_pattern or "").split()
	try:
		matched = list(target.chans(*patterns))
	except Exception:
		return {"error": "bad channel pattern %r" % (name_pattern,)}
	channels_truncated = len(matched) > MAX_CHOP_CHANNELS
	shown = matched[:MAX_CHOP_CHANNELS]
	num_samples = int(getattr(target, "numSamples", 0) or 0)
	clipped_start = min(start, num_samples)
	if shown:
		count = min(count, MAX_CHOP_VALUES // len(shown))
	clipped_end = min(clipped_start + count, num_samples)
	channels = []
	for chan in shown:
		try:
			values = [_sample(chan[i]) for i in range(clipped_start, clipped_end)]
		except Exception as exc:
			return {"error": "could not read channel %r of %s: %s" % (chan.name, path, exc)}
		channels.append({"name": chan.name, "values": values})
	next_offset = clipped_end if clipped_end < num_samples else None
	return {
		"path": target.path,
		"num_channels": int(getattr(target, "numChans", len(matched)) or 0),
		"matched_channels": len(matched),
		"channels_truncated": channels_truncated,
		"num_samples": num_samples,
		"rate": getattr(target, "rate", None),
		"start": clipped_start,
		"count": clipped_end - clipped_start,
		"next_offset": next_offset,
		"channels": channels,
	}


def _clip_text_dat(text, limit):
	"""Like _clip_text, but keeps internal whitespace: a DAT cell may hold structured text
	(a script, a path), which squashing would mangle."""
	return text if len(text) <= limit else text[: limit - 3] + "..."


def _without_token(text):
	"""The bridge's own script holds the bearer token, and a Select DAT can copy it elsewhere.
	Replaced before clipping, so no part of it survives a cut."""
	return text.replace(TOKEN, _REDACTED)


def _dat_cell(target, row, col):
	cell = target[row, col]
	value = _without_token(str(cell.val if cell is not None else ""))
	return _clip_text_dat(value, MAX_DAT_CELL_CHARS)


def _read_dat(pars):
	path = pars.get("path")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	if getattr(target, "family", None) != "DAT":
		return {"error": "%s is a %s, not a DAT." % (path, target.family)}
	is_table = bool(getattr(target, "isTable", True))
	if not is_table:
		text = _without_token(str(getattr(target, "text", "") or ""))
		return {
			"path": target.path,
			"is_table": False,
			"num_rows": 0,
			"num_cols": 0,
			"row_offset": 0,
			"row_limit": 0,
			"next_row_offset": None,
			"columns_truncated": False,
			"rows": [],
			"text": _clip_text_dat(text, MAX_DAT_TEXT_CHARS),
		}
	try:
		row_offset = int(pars.get("row_offset", 0))
		row_limit = int(pars.get("row_limit", DEFAULT_DAT_ROWS))
	except (TypeError, ValueError):
		return {"error": "row_offset and row_limit must be integers"}
	if row_offset < 0 or row_limit <= 0:
		return {"error": "row_offset must be >= 0 and row_limit must be > 0"}
	row_limit = min(row_limit, MAX_DAT_ROWS)
	num_rows = int(getattr(target, "numRows", 0) or 0)
	num_cols = int(getattr(target, "numCols", 0) or 0)
	columns_truncated = num_cols > MAX_DAT_COLS
	cols_shown = min(num_cols, MAX_DAT_COLS)
	row_offset = min(row_offset, num_rows)
	rows = []
	characters = 0
	for r in range(row_offset, min(row_offset + row_limit, num_rows)):
		try:
			row = [_dat_cell(target, r, c) for c in range(cols_shown)]
		except Exception as exc:
			return {"error": "could not read row %d of %s: %s" % (r, path, exc)}
		characters += sum(len(cell) for cell in row)
		if rows and characters > MAX_DAT_REPLY_CHARS:
			break
		rows.append(row)
	row_end = row_offset + len(rows)
	return {
		"path": target.path,
		"is_table": True,
		"num_rows": num_rows,
		"num_cols": num_cols,
		"row_offset": row_offset,
		"row_limit": len(rows),
		"next_row_offset": row_end if row_end < num_rows else None,
		"columns_truncated": columns_truncated,
		"rows": rows,
		"text": None,
	}


def _attribute_names(attribs):
	"""Attribute names from a td.Attributes collection.

	docs.derivative.ca documents only [name] access, but iterating one worked on 2025.33230
	(research/PHASE4_LIVE_RESULTS.md); P is not among them. A build where it cannot be
	iterated reports an empty list rather than failing the call.
	"""
	try:
		return sorted(attr.name for attr in attribs)
	except Exception:
		return []


def _position(value):
	if value is None:
		return None
	try:
		position = [float(value[0]), float(value[1]), float(value[2])]
	except Exception:
		return None
	return position if all(math.isfinite(v) for v in position) else None


def _group_names(groups):
	"""A SOP's group names, without TouchDesigner's own "__..." bookkeeping groups."""
	return sorted(name for name in (groups or {}) if not name.startswith("__"))


def _read_sop_info(pars):
	path = pars.get("path")
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	if getattr(target, "family", None) != "SOP":
		return {"error": "%s is a %s, not a SOP." % (path, target.family)}
	return {
		"path": target.path,
		"num_points": int(getattr(target, "numPoints", 0) or 0),
		"num_prims": int(getattr(target, "numPrims", 0) or 0),
		"num_vertices": int(getattr(target, "numVertices", 0) or 0),
		"min": _position(getattr(target, "min", None)),
		"max": _position(getattr(target, "max", None)),
		"size": _position(getattr(target, "size", None)),
		"point_groups": _group_names(getattr(target, "pointGroups", None)),
		"prim_groups": _group_names(getattr(target, "primGroups", None)),
		"point_attributes": _attribute_names(getattr(target, "pointAttribs", None) or ()),
		"prim_attributes": _attribute_names(getattr(target, "primAttribs", None) or ()),
		"vertex_attributes": _attribute_names(getattr(target, "vertexAttribs", None) or ()),
	}


def _split_list(value):
	return [item for item in (value or "").split(",") if item]


def _scaled_down(target, fmt):
	"""(data, width, height) of a TOP shrunk to the cell limit, without creating an operator.
	numpyArray() is float RGBA with the bottom row first (research/PHASE0_SPIKE_RESULTS.md)."""
	import cv2  # pyright: ignore[reportMissingImports]  # ships in TouchDesigner's Python
	import numpy  # pyright: ignore[reportMissingImports]

	scale = math.sqrt(MAX_CONTACT_CELL_PIXELS / (target.width * target.height))
	width, height = max(1, int(target.width * scale)), max(1, int(target.height * scale))
	pixels = target.numpyArray()
	small = cv2.resize(pixels[::-1], (width, height), interpolation=cv2.INTER_AREA)
	eight = (numpy.clip(numpy.nan_to_num(small), 0, 1) * 255 + 0.5).astype(numpy.uint8)
	if eight.ndim == 3:
		eight = eight[:, :, [2, 1, 0]]  # RGB(A) to the BGR order OpenCV writes
	encoded, data = cv2.imencode("." + fmt, eight)
	if not encoded:
		raise RuntimeError("OpenCV could not encode the image")
	return bytes(data), width, height


def _contact_cell(target, fmt):
	"""(data, width, height, None) or (None, 0, 0, error message)."""
	if target.width * target.height > MAX_PIXELS:
		return None, 0, 0, "%s is %dx%d, over the %d-pixel limit." % (
			target.path, target.width, target.height, MAX_PIXELS
		)
	if target.width * target.height > MAX_CONTACT_CELL_PIXELS:
		data, width, height = _scaled_down(target, fmt)
	else:
		data = bytes(target.saveByteArray("." + fmt))
		width, height = target.width, target.height
	if len(data) > MAX_CONTACT_CELL_BYTES:
		return None, 0, 0, (
			"%s encoded to %d bytes, over the %d-byte contact-sheet cell limit; use jpg."
			% (target.path, len(data), MAX_CONTACT_CELL_BYTES)
		)
	return data, width, height, None


def _frame_range_error(clock, frames):
	"""Why these frames cannot be shown, or None. TouchDesigner silently wraps a frame outside
	the playback range to another one."""
	low, high = clock.rangeStart, clock.rangeEnd
	for frame in frames:
		if frame != int(frame):
			return "frame %s is not a whole number" % frame
		if not low <= frame <= high:
			return "frame %d is outside the timeline's playback range %d to %d" % (frame, low, high)
	return None


def _contact_cells(pars):
	fmt = pars.get("format", "png")
	if fmt not in ("png", "jpg"):
		return {"error": "format must be png or jpg"}
	paths = _split_list(pars.get("paths"))
	if not paths:
		return {"error": "paths must list at least one absolute TOP path"}
	if len(paths) > MAX_CONTACT_PATHS:
		return {"error": "at most %d paths per call" % MAX_CONTACT_PATHS}
	targets = []
	for path in paths:
		target = _resolve_exact(path)
		if target is None:
			return _bad_path(path)
		if target.family != "TOP":
			return {"error": "%s is a %s, not a TOP." % (path, target.family)}
		targets.append(target)
	frames = []
	for token in _split_list(pars.get("frames")):
		try:
			frames.append(float(token))
		except ValueError:
			return {"error": "bad frame number %r" % (token,)}
	if len(frames) > MAX_CONTACT_FRAMES:
		return {"error": "at most %d frames per call" % MAX_CONTACT_FRAMES}
	combos = [(t, f) for t in targets for f in frames] if frames else [(t, None) for t in targets]
	if len(combos) > MAX_CONTACT_CELLS:
		return {"error": "%d cells requested, over the %d-cell limit" % (len(combos), MAX_CONTACT_CELLS)}
	clock = None
	if frames:
		clock = getattr(op("/"), "time", None)
		if clock is None:
			return {"error": "the project root has no Time Component, so frames cannot be shown"}
		error = _frame_range_error(clock, frames)
		if error:
			return {"error": error}
	original_frame = clock.frame if clock is not None else None
	cells = []
	total_bytes = 0
	try:
		for target, frame in combos:
			if clock is not None:
				clock.frame = frame
				if clock.frame != frame:
					return {"error": "TouchDesigner did not move to frame %d" % frame}
				target.cook(force=True)
			data, width, height, error = _contact_cell(target, fmt)
			if data is None:
				return {"error": error}
			total_bytes += len(data)
			if total_bytes > MAX_CONTACT_TOTAL_BYTES:
				return {
					"error": "the requested cells total over %d bytes; ask for fewer paths, "
					"frames, or use jpg." % MAX_CONTACT_TOTAL_BYTES
				}
			cells.append(
				{
					"path": target.path,
					"frame": frame,
					"width": width,
					"height": height,
					"data_b64": base64.b64encode(data).decode("ascii"),
				}
			)
	finally:
		if clock is not None:
			clock.frame = original_frame
	return {"format": fmt, "cells": cells}


class _Refused(Exception):
	"""A change request that was turned down before anything was touched."""


def _clip(value):
	if isinstance(value, str) and len(value) > MAX_AUDIT_STRING:
		return value[:MAX_AUDIT_STRING] + "...(truncated)"
	if isinstance(value, dict):
		return {str(k): _clip(v) for k, v in value.items()}
	if isinstance(value, list):
		return [_clip(v) for v in value]
	return value


def _audit(entry):
	"""Append one JSON line to the audit log, readable by this user only. Raises OSError."""
	record = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
	record.update(_clip(entry))
	line = (json.dumps(record, default=str) + "\n").encode("utf-8")
	folder = os.path.dirname(AUDIT_LOG)
	if folder:
		os.makedirs(folder, mode=0o700, exist_ok=True)
	handle = os.open(AUDIT_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
	try:
		os.write(handle, line)
	finally:
		os.close(handle)


def _audit_or_warn(entry):
	"""Audit something that has already happened. Returns a warning string if it was not logged."""
	try:
		_audit(entry)
	except OSError as exc:
		message = "the audit log at %s could not be written: %s" % (AUDIT_LOG, exc)
		print("[td-mcp bridge] " + message)
		return message
	return None


def _audit_or_refuse(entry):
	try:
		_audit(entry)
	except OSError as exc:
		raise _Refused("cannot write the audit log at %s (%s). Nothing was changed." % (AUDIT_LOG, exc))


def _without_values(body):
	"""A refused request, for the log. Parameter values are left out: without a resolved
	parameter there is no way to know whether one is a password."""
	if not isinstance(body, dict):
		return None
	safe = dict(body)
	changes = safe.get("parameters")
	if isinstance(changes, list):
		safe["parameters"] = [
			{k: (v if k == "name" else "<omitted>") for k, v in c.items()} if isinstance(c, dict) else "<omitted>"
			for c in changes
		]
	elif changes is not None:
		safe["parameters"] = "<omitted>"
	return safe


def _stack(name):
	return list(getattr(ui.undo, name))


def _undo_marker(isUndo, info):
	"""Does nothing. Adding it first gives every change exactly one undo entry under our label:
	without it, TouchDesigner renames a block that only sets an expression, and a change that
	alters nothing leaves no entry (research/PHASE2_UNDO_RESULTS.md)."""
	return


def _rolled_back():
	return list(parent().fetch(ROLLED_BACK_KEY, [], search=False))


def _roll_back(label):
	"""Undo a failed change. True only if our own entry was on top and is now gone."""
	try:
		stack = _stack("undoStack")
		if not stack or stack[0] != label:
			return False
		parent().store(ROLLED_BACK_KEY, (_rolled_back() + [label])[-MAX_ROLLED_BACK:])
		ui.undo.undo()
		return True
	except Exception:
		print("[td-mcp bridge] rollback of %r failed:\n%s" % (label, traceback.format_exc()))
		return False


def _require_undo():
	if not ui.undo.globalState:
		raise _Refused("undo is disabled in TouchDesigner, so the change could not be reversed. Nothing was changed.")


def _apply_change(context, summary, request, apply):
	"""Run `apply` as one named undo entry, audited before and after. Rolls back on failure."""
	_require_undo()
	label = "%s%s: %s %s" % (UNDO_PREFIX, context["id"], context["action"], summary)
	log = {"id": context["id"], "action": context["action"], "request": request}
	_audit_or_refuse(dict(log, event="started"))
	failure = None
	result = {}
	ui.undo.startBlock(label)
	try:
		ui.undo.addCallback(_undo_marker, label)
		result = apply()
	except Exception as exc:
		failure = str(exc) if isinstance(exc, _Refused) else "%s: %s" % (type(exc).__name__, exc)
		if not isinstance(exc, _Refused):
			print("[td-mcp bridge] %s failed:\n%s" % (context["action"], traceback.format_exc()))
	finally:
		# Some operators end the block themselves while being created (Annotate COMP).
		with contextlib.suppress(Exception):
			ui.undo.endBlock()
	if failure is not None:
		if _roll_back(label):
			failure += ". Nothing was changed."
		else:
			failure += ". The change could NOT be rolled back and may be partly applied; check the network."
		_audit_or_warn(dict(log, event="failed", error=failure))
		return {"error": failure}
	stack = _stack("undoStack")
	result["undo_label"] = label if stack and stack[0] == label else None
	result["audit_warning"] = _audit_or_warn(dict(log, event="ok", result=result))
	return result


def _is_protected(path):
	return _is_under(path, INTERNAL_ROOTS + (parent().path,))


def _text(body, key):
	value = body.get(key)
	if not isinstance(value, str) or not value:
		raise _Refused("%s must be a non-empty string" % key)
	return value


def _integer(body, key, default):
	value = body.get(key, default)
	if value is None:
		return default
	if isinstance(value, bool) or not isinstance(value, int):
		raise _Refused("%s must be an integer" % key)
	return value


def _only_keys(body, *allowed):
	unknown = sorted(set(body) - set(allowed))
	if unknown:
		raise _Refused("unknown field(s): %s" % ", ".join(unknown))


def _changeable(path):
	"""The operator at exactly `path`, if this bridge is allowed to change it."""
	target = _resolve_exact(path)
	if target is None:
		raise _Refused(_bad_path(path)["error"])
	if _is_protected(target.path):
		raise _Refused(
			"%s is protected: the bridge's own component and TouchDesigner's /ui and /sys "
			"networks cannot be changed through the bridge." % target.path
		)
	return target


def _parent_path(path):
	return path.rsplit("/", 1)[0] or "/"


def _all_children(comp):
	return list(comp.findChildren(depth=1, includeUtility=True))


def _suggest(wanted, names):
	"""Closest valid names: exact prefix matches first ("noise" -> noiseTOP, noiseCHOP)."""
	lowered = wanted.lower()
	prefixed = [n for n in sorted(names) if n.lower().startswith(lowered)]
	close = difflib.get_close_matches(wanted, sorted(names), n=6, cutoff=0.5)
	return list(dict.fromkeys(prefixed + close))[:8]


def _operator_types():
	return {t.__name__ for group in families.values() for t in group}


def _unknown_type(optype, valid):
	close = _suggest(optype, valid)
	return "unknown operator type %r. Types are written with their family, such as noiseTOP.%s" % (
		optype, " Closest: %s." % ", ".join(close) if close else ""
	)


def _create(body, context):
	_only_keys(body, "parent", "type", "name", "x", "y")
	parent_path = _text(body, "parent")
	optype = _text(body, "type")
	name = body.get("name")
	box = _changeable(parent_path)
	if getattr(box, "family", None) != "COMP":
		raise _Refused("%s is a %s. Operators can only be created inside a COMP." % (box.path, box.family))
	valid = _operator_types()
	if optype not in valid:
		raise _Refused(_unknown_type(optype, valid))
	if name is not None and (not isinstance(name, str) or not _NAME_RULE.fullmatch(name)):
		raise _Refused("name may contain only letters, digits and underscores")
	x, y = body.get("x"), body.get("y")
	if (x is None) != (y is None):
		raise _Refused("give both x and y, or neither")
	if x is not None:
		x, y = _integer(body, "x", 0), _integer(body, "y", 0)

	def apply():
		before = _all_children(box)
		taken = {o.path for o in before}
		if x is None:
			# To the right of everything already there, so new nodes do not stack on old ones.
			placed = [o for o in before if getattr(o, "dock", None) is None]
			if placed:
				rightmost = max(placed, key=lambda o: o.nodeX + o.nodeWidth)
				want = (rightmost.nodeX + rightmost.nodeWidth + LAYOUT_GAP_X, rightmost.nodeY)
			else:
				want = (0, 0)
		else:
			want = (x, y)
		made = box.create(optype, name) if name else box.create(optype)
		extras = [o for o in _all_children(box) if o.path not in taken and o.path != made.path]
		# Docked operators do not follow their host, so move them by the same amount.
		dx, dy = want[0] - made.nodeX, want[1] - made.nodeY
		for item in [made] + extras:
			item.nodeX += dx
			item.nodeY += dy
		return {
			"path": made.path,
			"type": made.type,
			"family": made.family,
			"requested_name": name,
			# TouchDesigner renames silently when a name is taken.
			"renamed": bool(name) and made.name != name,
			"x": made.nodeX,
			"y": made.nodeY,
			"also_created": sorted(o.path for o in extras),
		}

	summary = "%s in %s" % (optype, box.path)
	return _apply_change(context, summary, dict(body), apply)


def _connector(connectors, index, path, side):
	if not 0 <= index < len(connectors):
		have = "none" if not connectors else "0 to %d" % (len(connectors) - 1)
		raise _Refused("%s has no %s connector %d (available: %s)" % (path, side, index, have))
	return connectors[index]


def _sources(connector):
	return [c.owner.path for c in connector.connections]


def _connect(body, context):
	_only_keys(body, "source", "target", "source_output", "target_input")
	source = _changeable(_text(body, "source"))
	target = _changeable(_text(body, "target"))
	out_index = _integer(body, "source_output", 0)
	in_index = _integer(body, "target_input", 0)
	if source.path == target.path:
		raise _Refused("an operator cannot be wired to itself")
	if _parent_path(source.path) != _parent_path(target.path):
		# TouchDesigner does not refuse this: it removes the existing wire and connects nothing.
		raise _Refused(
			"%s and %s are in different networks; wires only join operators in the same COMP. "
			"Use a Select operator or an In/Out operator instead." % (source.path, target.path)
		)
	_connector(source.outputConnectors, out_index, source.path, "output")
	replaced = _sources(_connector(target.inputConnectors, in_index, target.path, "input"))

	def apply():
		try:
			source.outputConnectors[out_index].connect(target.inputConnectors[in_index])
		except Exception as exc:
			raise _Refused(
				"TouchDesigner refused to wire %s (%s) into %s (%s): %s"
				% (source.path, source.family, target.path, target.family, type(exc).__name__)
			)
		connectors = target.inputConnectors
		wired = connectors[in_index].connections if in_index < len(connectors) else []
		if not any(c.owner.path == source.path and c.index == out_index for c in wired):
			raise _Refused("TouchDesigner did not make the connection")
		return {
			"source": source.path,
			"target": target.path,
			"source_output": out_index,
			"target_input": in_index,
			"replaced": [p for p in replaced if p != source.path],
			"inputs": _io_paths(target.inputs),
		}

	return _apply_change(context, "%s -> %s" % (source.path, target.path), dict(body), apply)


def _disconnect(body, context):
	_only_keys(body, "target", "target_input")
	target = _changeable(_text(body, "target"))
	in_index = _integer(body, "target_input", 0)
	removed = _sources(_connector(target.inputConnectors, in_index, target.path, "input"))
	if not removed:
		raise _Refused("nothing is connected to input %d of %s" % (in_index, target.path))

	def apply():
		before = len(target.inputs)
		target.inputConnectors[in_index].disconnect()
		if len(target.inputs) >= before:
			raise _Refused("TouchDesigner did not remove the connection")
		# On multi-input operators the later inputs move up, so report what is wired now.
		return {
			"target": target.path,
			"target_input": in_index,
			"removed": removed,
			"inputs": _io_paths(target.inputs),
		}

	return _apply_change(context, "%s input %d" % (target.path, in_index), dict(body), apply)


def _mode_name(par):
	return str(par.mode).rsplit(".", 1)[-1]


def _is_secret(par):
	return bool(getattr(par, "password", False))


def _par_state(par):
	try:
		value = _jsonable(par.eval())
	except Exception:
		value = None
	state = {
		"mode": _mode_name(par),
		"value": value,
		"expr": par.expr or None,
		"bind": par.bindExpr or None,
	}
	if _is_secret(par):
		state.update({k: (_REDACTED if state[k] else state[k]) for k in ("value", "expr", "bind")})
	return state


def _checked_value(par, value):
	"""`value` as TouchDesigner should receive it. TouchDesigner coerces many wrong values
	silently (an unknown menu entry, "yes" for a toggle), so they are refused here."""
	name = par.name
	is_bool = isinstance(value, bool)
	if par.isMenu and not par.isString:
		names = list(par.menuNames or [])
		if isinstance(value, str) and value in names:
			return value
		if isinstance(value, int) and not is_bool and 0 <= value < len(names):
			return value
		raise _Refused("%s is a menu. Use one of: %s" % (name, ", ".join(names)))
	if par.isToggle:
		if is_bool or (isinstance(value, int) and value in (0, 1)):
			return bool(value)
		raise _Refused("%s is a toggle. Use true or false." % name)
	if par.isNumber:
		if is_bool or not isinstance(value, (int, float)) or not math.isfinite(value):
			raise _Refused("%s is a number. For an expression, use expr instead of value." % name)
		if par.isInt:
			if value != int(value):
				raise _Refused("%s is a whole number" % name)
			return int(value)
		return value
	if not isinstance(value, str):
		raise _Refused("%s takes a string" % name)
	return value


def _plan_parameter(target, change):
	if not isinstance(change, dict):
		raise _Refused("each parameter change must be an object")
	_only_keys(change, "name", "value", "expr", "bind")
	name = _text(change, "name")
	kinds = [k for k in ("value", "expr", "bind") if k in change]
	if len(kinds) != 1:
		raise _Refused("%s needs exactly one of value, expr or bind" % name)
	kind = kinds[0]
	par = target.par[name]
	if par is None:
		close = _suggest(name, {p.name for p in _all_pars(target)})
		raise _Refused(
			"%s has no parameter %r.%s" % (target.path, name, " Closest: %s." % ", ".join(close) if close else "")
		)
	if par.readOnly:
		raise _Refused("%s is read-only" % name)
	if par.isPulse:
		raise _Refused("%s is a pulse button; this tool does not press buttons" % name)
	if _mode_name(par) == "EXPORT":
		source = getattr(par.exportOP, "path", "another operator")
		raise _Refused("%s is driven by an export from %s. Turn that export off first." % (name, source))
	if kind == "value":
		new = _checked_value(par, change["value"])
	else:
		new = _text(change, kind)
	return par, kind, new


def _set_one(par, kind, new):
	if kind == "value":
		# Writing the value of a bound parameter writes through to the bind master, which may be
		# another operator. Switching to constant first changes this parameter only.
		if _mode_name(par) != "CONSTANT":
			par.mode = ParMode.CONSTANT
		par.val = new
	elif kind == "expr":
		par.expr = new
		par.mode = ParMode.EXPRESSION
	else:
		par.bindExpr = new
		par.mode = ParMode.BIND


def _verify_one(target, par, kind, new):
	"""After the whole batch is applied: fail on a dead expression or bind, and say when
	TouchDesigner stored something other than what was asked (clamping)."""
	notes = {"adjusted": False, "warning": None}
	if kind == "bind":
		master = par.bindMaster
		if master is None:
			raise _Refused("bind for %s does not resolve to anything" % par.name)
		if getattr(master, "name", None) == par.name and getattr(getattr(master, "owner", None), "path", None) == target.path:
			raise _Refused("%s cannot be bound to itself" % par.name)
	if kind in ("expr", "bind"):
		try:
			value = par.eval()
		except Exception as exc:
			raise _Refused("%s for %s does not evaluate: %s" % (kind, par.name, exc))
		if isinstance(value, float) and not math.isfinite(value):
			# It does evaluate, so it is applied; an expression can be non-finite only for now.
			notes["warning"] = "the %s evaluates to %s" % (
				kind, _REDACTED if _is_secret(par) else value
			)
		return notes
	stored = par.eval()
	if par.isMenu and not par.isString:
		return notes
	if par.isNumber and not par.isToggle:
		notes["adjusted"] = not math.isclose(float(stored), float(new), rel_tol=1e-6, abs_tol=1e-9)
	elif getattr(par, "isOP", False) and new and stored is None:
		notes["warning"] = "%r does not resolve to an operator" % new
	return notes


def _set_parameters(body, context):
	_only_keys(body, "path", "parameters")
	path = _text(body, "path")
	requested = body.get("parameters")
	if not isinstance(requested, list) or not requested:
		raise _Refused("parameters must be a non-empty list")
	if len(requested) > MAX_PARAMETER_CHANGES:
		raise _Refused("at most %d parameter changes per call" % MAX_PARAMETER_CHANGES)
	target = _changeable(path)
	plan = [_plan_parameter(target, change) for change in requested]
	names = [par.name for par, _kind, _new in plan]
	if len(set(names)) != len(names):
		raise _Refused("each parameter may appear only once per call")
	logged = {
		"path": target.path,
		"parameters": [
			{"name": par.name, kind: _REDACTED if _is_secret(par) else new} for par, kind, new in plan
		],
	}

	def apply():
		previous = [_par_state(par) for par, _kind, _new in plan]
		for par, kind, new in plan:
			try:
				_set_one(par, kind, new)
			except Exception as exc:
				detail = type(exc).__name__ if _is_secret(par) else "%s: %s" % (type(exc).__name__, exc)
				raise _Refused("could not set %s (%s)" % (par.name, detail))
		rows = []
		for (par, kind, new), before in zip(plan, previous):
			notes = _verify_one(target, par, kind, new)
			row = {"name": par.name, "previous": before}
			row.update(_par_state(par))
			row.update(notes)
			rows.append(row)
		return {"path": target.path, "parameters": rows}

	return _apply_change(context, "%s [%s]" % (target.path, ", ".join(names)), logged, apply)


def _delete(body, context):
	_only_keys(body, "path")
	target = _changeable(_text(body, "path"))
	path = target.path
	if path == "/":
		raise _Refused("the project root cannot be deleted")
	if _is_under(path, UNDELETABLE_ROOTS):
		raise _Refused("%s holds project settings and cannot be deleted through the bridge" % path)
	if _is_under(parent().path, (path,)):
		raise _Refused("%s contains the bridge itself and cannot be deleted through the bridge" % path)

	def apply():
		box = op(_parent_path(path))
		before = {o.path for o in _all_children(box)}
		kind, family = target.type, target.family
		descendants = len(target.findChildren(includeUtility=True)) if family == "COMP" else 0
		inputs, outputs = _io_paths(target.inputs), _io_paths(target.outputs)
		downstream = list(dict.fromkeys(o for o in (target.outputs or []) if o is not None))
		target.destroy()
		if _resolve_exact(path) is not None:
			raise _Refused("TouchDesigner did not delete %s" % path)
		gone = before - {o.path for o in _all_children(box)} - {path}
		return {
			"deleted": path,
			"type": kind,
			"family": family,
			"descendants": descendants,
			"disconnected_inputs": inputs,
			"disconnected_outputs": outputs,
			# TouchDesigner may wire a deleted operator's source straight into its outputs.
			"downstream": [
				{"path": o.path, "inputs": _io_paths(o.inputs)} for o in downstream if o.path not in gone
			],
			"also_deleted": sorted(gone),  # operators docked to the deleted one go with it
		}

	return _apply_change(context, path, dict(body), apply)


def _layers(nodes, sources):
	"""Column for each node: one more than its furthest upstream node. Wires that close a loop
	are ignored. Iterative, so a very long chain cannot overflow the stack."""
	depth = {}
	visiting = set()
	for start in nodes:
		pending = [start]
		while pending:
			node = pending[-1]
			if node in depth:
				pending.pop()
				continue
			visiting.add(node)
			waiting = [s for s in sources[node] if s not in depth and s not in visiting]
			if waiting:
				pending.extend(waiting)
				continue
			depth[node] = 1 + max((depth[s] for s in sources[node] if s in depth), default=-1)
			visiting.discard(node)
			pending.pop()
	return depth


def _layout(body, context):
	_only_keys(body, "path")
	path = _text(body, "path")
	box = _changeable(path)
	if getattr(box, "family", None) != "COMP":
		raise _Refused("%s is a %s, not a COMP" % (box.path, box.family))
	movable, skipped = {}, []
	for child in _all_children(box):
		if _is_protected(child.path) or child.type == "annotate":
			skipped.append(child.path)
		elif getattr(child, "dock", None) is None:
			movable[child.path] = child
	if not movable:
		raise _Refused("%s has no operators to arrange" % box.path)
	if len(movable) > MAX_LAYOUT_OPERATORS:
		raise _Refused("%s has more than %d operators" % (box.path, MAX_LAYOUT_OPERATORS))

	def apply():
		order = sorted(movable)
		sources = {p: [i.path for i in movable[p].inputs if i is not None and i.path in movable] for p in order}
		depth = _layers(order, sources)
		docked = {p: list(movable[p].docked) for p in order}
		# A node's block is the node plus whatever is docked around it.
		extent = {}
		for p in order:
			items = [movable[p]] + docked[p]
			node = movable[p]
			extent[p] = (
				min(i.nodeX for i in items) - node.nodeX,
				min(i.nodeY for i in items) - node.nodeY,
				max(i.nodeX + i.nodeWidth for i in items) - node.nodeX,
				max(i.nodeY + i.nodeHeight for i in items) - node.nodeY,
			)
		# Keep the network's top-left corner where it was, so the view does not jump.
		left = min(movable[p].nodeX + extent[p][0] for p in order)
		top = max(movable[p].nodeY + extent[p][3] for p in order)
		row = {}
		moved = []
		column_x = left
		for column in range(max(depth.values()) + 1):
			members = [p for p in order if depth[p] == column]
			if not members:
				continue

			def rank(p):
				# Next to the inputs' rows keeps wires short; otherwise keep the old order.
				rows = [row[s] for s in sources[p] if s in row]
				return (sum(rows) / len(rows) if rows else float("inf"), -movable[p].nodeY, p)

			cursor = top
			for index, p in enumerate(sorted(members, key=rank)):
				row[p] = index
				node = movable[p]
				low_x, low_y, _high_x, high_y = extent[p]
				new_x = int(column_x - low_x)
				new_y = int(cursor - high_y)
				dx, dy = new_x - node.nodeX, new_y - node.nodeY
				if dx or dy:
					for item in [node] + docked[p]:
						item.nodeX += dx
						item.nodeY += dy
				moved.append({"path": p, "x": new_x, "y": new_y})
				cursor = new_y + low_y - LAYOUT_GAP_Y
			column_x += max(extent[p][2] - extent[p][0] for p in members) + LAYOUT_GAP_X
		return {"path": box.path, "operators": moved, "skipped": sorted(skipped)}

	return _apply_change(context, box.path, dict(body), apply)


def _history(context, body, redo):
	"""Step through this bridge's own undo entries, and never anyone else's."""
	_only_keys(body, "steps")
	steps = _integer(body, "steps", 1)
	if not 1 <= steps <= MAX_UNDO_STEPS:
		raise _Refused("steps must be 1 to %d" % MAX_UNDO_STEPS)
	_require_undo()
	verb = "redo" if redo else "undo"
	stack_name = "redoStack" if redo else "undoStack"

	def blocked():
		stack = _stack(stack_name)
		if not stack:
			return "there is nothing to %s" % verb
		top = stack[0]
		if not top.startswith(UNDO_PREFIX):
			return (
				"the next %s entry is %r, which this server did not make. %s it in TouchDesigner "
				"if you want to, then call again." % (verb, top, verb.capitalize())
			)
		if redo and top in _rolled_back():
			return "%r failed and was rolled back, so it will not be redone" % top
		return None

	reason = blocked()
	if reason:
		raise _Refused(reason)
	log = {"id": context["id"], "action": context["action"], "request": dict(body)}
	_audit_or_refuse(dict(log, event="started"))
	done = []
	while len(done) < steps:
		reason = blocked()
		if reason:
			break
		before = _stack(stack_name)
		try:
			ui.undo.redo() if redo else ui.undo.undo()
		except Exception as exc:
			reason = "TouchDesigner could not %s %r: %s" % (verb, before[0], exc)
			break
		if _stack(stack_name) == before:
			reason = "TouchDesigner did not %s %r" % (verb, before[0])
			break
		done.append(before[0])
	if not done:
		_audit_or_warn(dict(log, event="failed", error=reason))
		return {"error": reason}
	upcoming = blocked()
	result = {
		"action": verb,
		"done": done,
		"stopped": reason if len(done) < steps else None,
		"next": None if upcoming else _stack(stack_name)[0],
	}
	result["audit_warning"] = _audit_or_warn(dict(log, event="ok", result=result))
	return result


def _undo(body, context):
	return _history(context, body, redo=False)


def _redo(body, context):
	return _history(context, body, redo=True)


def _frame_number(body, key):
	value = body.get(key)
	if (
		isinstance(value, bool)
		or not isinstance(value, (int, float))
		or not math.isfinite(value)
		or value != int(value)
	):
		raise _Refused("%s must be a whole number" % key)
	return int(value)


# In the order they are written. A frame set before its range is wrapped into the old one, and
# one set before playback stops moves on by a frame (research/PHASE4_LIVE_RESULTS.md).
TIMELINE_ATTRIBUTES = ("start", "end", "rangeStart", "rangeEnd", "play", "frame")


def _timeline_state(clock):
	return {
		"frame": clock.frame,
		"rate": clock.rate,
		"start": clock.start,
		"end": clock.end,
		"range_start": clock.rangeStart,
		"range_end": clock.rangeEnd,
		"play": bool(clock.play),
	}


def _write_timeline(clock, values):
	for name in TIMELINE_ATTRIBUTES:
		if name in values:
			setattr(clock, name, values[name])


def _restore_timeline(isUndo, info):
	"""Undo callback: TouchDesigner's undo does not cover Time Component attributes set from
	Python (research/PHASE4_LIVE_RESULTS.md)."""
	_write_timeline(op("/").time, info["before" if isUndo else "after"])


def _timeline(body, context):
	_only_keys(body, "play", "frame", "start", "end")
	if not body:
		raise _Refused("give at least one of play, frame, start or end")
	clock = getattr(op("/"), "time", None)
	if clock is None:
		raise _Refused("the project root has no Time Component to control")
	changes = {}
	if "play" in body:
		if not isinstance(body["play"], bool):
			raise _Refused("play must be true or false")
		changes["play"] = body["play"]
	for key in ("start", "end", "frame"):
		if key in body:
			changes[key] = _frame_number(body, key)
	# The playhead stays inside a second, narrower range, so a new start or end moves both.
	if "start" in changes:
		changes["rangeStart"] = changes["start"]
	if "end" in changes:
		changes["rangeEnd"] = changes["end"]
	low = changes.get("rangeStart", clock.rangeStart)
	high = changes.get("rangeEnd", clock.rangeEnd)
	ranged = "start" in changes or "end" in changes
	if ranged and (low > high or changes.get("start", clock.start) > changes.get("end", clock.end)):
		raise _Refused("the range would end before it starts; give both start and end")
	if "frame" in changes and not low <= changes["frame"] <= high:
		raise _Refused("frame %d is outside the playback range %d to %d" % (changes["frame"], low, high))
	if ranged and "frame" not in changes and not low <= clock.frame <= high:
		# TouchDesigner would move the playhead only after this reply, to a frame of its choosing.
		changes["frame"] = low

	def apply():
		previous = _timeline_state(clock)
		touched = set(changes)
		if ranged:
			touched.add("frame")  # TouchDesigner moves the playhead into a new range
		info = {"before": {name: getattr(clock, name) for name in touched}, "after": {}}
		ui.undo.addCallback(_restore_timeline, info)  # first, so a failure below rolls back
		_write_timeline(clock, changes)
		for name, value in changes.items():
			# While playing, the frame moves on by itself.
			if getattr(clock, name) != value and not (name == "frame" and clock.play):
				raise _Refused("TouchDesigner did not set the timeline's %s to %s" % (name, value))
		info["after"] = {name: getattr(clock, name) for name in touched}
		return {"previous": previous, "timeline": _timeline_state(clock)}

	summary = ", ".join("%s=%s" % (key, body[key]) for key in sorted(body))
	return _apply_change(context, summary, dict(body), apply)


def _run_change(route, body):
	context = {"id": secrets.token_hex(4), "action": route.lstrip("/")}
	try:
		return CHANGE_ROUTES[route](body, context)
	except _Refused as exc:
		_audit_or_warn(
			{
				"id": context["id"],
				"action": context["action"],
				"event": "refused",
				"request": _without_values(body),
				"error": str(exc),
			}
		)
		return {"error": str(exc)}


def _read_body(request):
	"""The request's JSON object, or a string saying why it is unusable."""
	content_type = _header(request, "content-type")
	if not isinstance(content_type, str) or content_type.split(";")[0].strip().lower() != "application/json":
		return "Content-Type must be application/json"
	data = request.get("data")
	if isinstance(data, str):
		data = data.encode("utf-8")
	if not isinstance(data, (bytes, bytearray)) or not data:
		return "the request body is empty"
	if len(data) > MAX_BODY_BYTES:
		return "the request body is over %d bytes" % MAX_BODY_BYTES
	try:
		body = json.loads(bytes(data).decode("utf-8"))
	except (UnicodeDecodeError, ValueError):
		return "the request body is not valid JSON"
	if not isinstance(body, dict):
		return "the request body must be a JSON object"
	return body


ROUTES = {
	"/ping": _ping,
	"/capture": _capture,
	"/project-info": _project_info,
	"/errors": _errors,
	"/snapshot": _snapshot,
	"/children": _children,
	"/operator": _operator,
	"/parameters": _parameters,
	"/describe-type": _describe_type,
	"/performance": _performance,
	"/chop": _read_chop,
	"/dat": _read_dat,
	"/sop-info": _read_sop_info,
	"/contact-cells": _contact_cells,
}
if ENABLE_TEST_FIXTURES:
	ROUTES["/ensure-demo"] = _ensure_demo_route
	ROUTES["/foreign-edit"] = _foreign_edit_route
CHANGE_ROUTES = {
	"/create": _create,
	"/connect": _connect,
	"/disconnect": _disconnect,
	"/set-parameters": _set_parameters,
	"/delete": _delete,
	"/layout": _layout,
	"/undo": _undo,
	"/redo": _redo,
	"/timeline": _timeline,
}


def onServerStart(webServerDAT):
	return


def onServerStop(webServerDAT):
	return


def _handle(request, response):
	def reply(status, reason, body):
		response["statusCode"] = status
		response["statusReason"] = reason
		response["Content-Type"] = "application/json"
		# Never a wildcard or echoed Origin: this bridge sends no CORS header at all, so a
		# browser treats every cross-origin reply as unreadable regardless of this check.
		out = dict(body)
		out.setdefault("api_version", API_VERSION)
		response["data"] = json.dumps(out)
		return response

	if TOKEN == _TOKEN_PLACEHOLDER:
		return reply(503, "Service Unavailable", {"error": "bridge was not built with a token; run build_spike.py"})

	# Host/Origin checks run before the token (decision 0003): a forged or rebound DNS name
	# in Host, or a cross-origin browser request carrying Origin, is refused outright, rather
	# than reaching the token check at all.
	if not _loopback_netloc(_header(request, "host")):
		return reply(403, "Forbidden", {"error": "Host must be 127.0.0.1"})
	origin = _header(request, "origin")
	if origin is not None and not _loopback_netloc(origin):
		return reply(403, "Forbidden", {"error": "Origin must be loopback"})

	supplied = _header(request, "authorization")
	expected = ("Bearer " + TOKEN).encode("utf-8")
	if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode("utf-8"), expected):
		return reply(401, "Unauthorized", {"error": "missing or wrong bearer token"})

	uri = request.get("uri")
	path = uri.get("path") if isinstance(uri, dict) else str(uri)
	route = (path or "").split("?")[0]
	reader = ROUTES.get(route)
	changes = route in CHANGE_ROUTES
	if reader is None and not changes:
		return reply(404, "Not Found", {"error": "no route " + route})
	method = str(request.get("method") or "GET").upper()
	if method != ("POST" if changes else "GET"):
		return reply(405, "Method Not Allowed", {"error": "%s needs %s" % (route, "POST" if changes else "GET")})
	body: object = None
	if changes:
		body = _read_body(request)
		if isinstance(body, str):
			_audit_or_warn({"action": route.lstrip("/"), "event": "refused", "error": body})
			return reply(400, "Bad Request", {"error": body})

	started = time.perf_counter()
	try:
		if reader is None:
			result = _run_change(route, body)
		else:
			result = reader(request.get("pars") or {})
	except Exception as exc:
		# Short message to the caller; the traceback (file paths, source layout) stays in the textport.
		print("[td-mcp bridge] %s failed:\n%s" % (route, traceback.format_exc()))
		return reply(500, "Internal Server Error", {"error": "%s: %s" % (type(exc).__name__, exc)})
	return reply(200, "OK", {"route": route, "handler_ms": round((time.perf_counter() - started) * 1000, 2), "result": result})


def onHTTPRequest(webServerDAT, request, response):
	# An exception escaping this callback leaves the client waiting with no reply at all,
	# so everything is wrapped.
	try:
		return _handle(request, response)
	except Exception:
		# This can run before authentication, so nothing about the failure goes to the caller.
		# The traceback goes to TouchDesigner's textport instead.
		print("[td-mcp bridge] unhandled error:\n" + traceback.format_exc())
		response["statusCode"] = 500
		response["statusReason"] = "Internal Server Error"
		response["Content-Type"] = "application/json"
		response["data"] = json.dumps(
			{"error": "unhandled bridge error; see TouchDesigner's textport", "api_version": API_VERSION}
		)
		return response
