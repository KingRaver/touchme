"""TouchDesigner bridge (Phase 0 round trip, Phase 1 core and perception).

Web Server DAT callbacks. The DAT binds to 127.0.0.1 (set by bridge/build_spike.py).
Every request needs a loopback Host, a loopback-or-absent Origin, and
`Authorization: Bearer <token>`. Callbacks run on the main thread
(research/PHASE0_SPIKE_RESULTS.md), so operators are touched directly.

Routes: GET /ping, GET /capture?path=<absolute TOP path>&format=png|jpg,
GET /project-info, GET /errors?path=<absolute path>,
GET /snapshot?path=<absolute path>&maxdepth=<int>,
GET /children?path=<absolute path>&family=<str>&type=<str>&offset=<int>&limit=<int>,
GET /operator?path=<absolute path>, GET /parameters?path=<absolute path>&name=<pattern>.
Test builds only (build_spike.py --test-fixtures): GET /ensure-demo, which creates a demo TOP
inside the bridge's own container. Default builds are read-only.
"""

import base64
import hmac
import json
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

TOKEN = "__TOKEN__"
_FIXTURES_FLAG = "__TEST_FIXTURES__"  # "1" or "0", set by build_spike.py
ENABLE_TEST_FIXTURES = _FIXTURES_FLAG == "1"
# 0.2.0 added the `excluded` field to /errors and /snapshot, which the server's output schemas
# require, so a server that requires 0.2.0 refuses an older bridge with a rebuild message.
API_VERSION = "0.2.0"
# Built from pieces so build_spike.py's replace of the token placeholder cannot rewrite it.
_TOKEN_PLACEHOLDER = "__TOK" + "EN__"
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
	"""A Par's evaluated value is a number/string/bool, or an OP for an OP-type parameter."""
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
		return getattr(par, limit_name) if getattr(par, clamp_name) else None
	except Exception:
		return None


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
	target = _resolve_exact(path)
	if target is None:
		return _bad_path(path)
	all_pars = _all_pars(target)
	if name_pattern:
		try:
			allowed = {p.name for p in target.pars(name_pattern)}
		except Exception:
			return {"error": "bad name pattern %r" % (name_pattern,)}
		all_pars = [p for p in all_pars if p.name in allowed]
	parameters = [_par_detail(p) for p in all_pars]
	return {"path": target.path, "parameter_count": len(parameters), "parameters": parameters}


ROUTES = {
	"/ping": _ping,
	"/capture": _capture,
	"/project-info": _project_info,
	"/errors": _errors,
	"/snapshot": _snapshot,
	"/children": _children,
	"/operator": _operator,
	"/parameters": _parameters,
}
if ENABLE_TEST_FIXTURES:
	ROUTES["/ensure-demo"] = _ensure_demo_route


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
	handler = ROUTES.get(route)
	if handler is None:
		return reply(404, "Not Found", {"error": "no route " + route})

	started = time.perf_counter()
	try:
		result = handler(request.get("pars") or {})
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
