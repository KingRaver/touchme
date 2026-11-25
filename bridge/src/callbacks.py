"""TouchDesigner bridge, Phase 0 round-trip spike.

Web Server DAT callbacks. The DAT binds to 127.0.0.1 (set by bridge/build_spike.py).
Every request needs `Authorization: Bearer <token>`. Callbacks run on the main thread
(research/PHASE0_SPIKE_RESULTS.md), so operators are touched directly.

Routes: GET /ping, GET /capture?path=<absolute TOP path>&format=png|jpg
Test builds only (build_spike.py --test-fixtures): GET /ensure-demo, which creates a demo TOP
inside the bridge's own container. Default builds are read-only.
"""

import base64
import hmac
import json
import time
import traceback

TOKEN = "__TOKEN__"
_FIXTURES_FLAG = "__TEST_FIXTURES__"  # "1" or "0", set by build_spike.py
ENABLE_TEST_FIXTURES = _FIXTURES_FLAG == "1"
API_VERSION = "0.0.1"
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


def _header(request, name):
	for key, value in request.items():
		if isinstance(key, str) and key.lower() == name:
			return value
	return None


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


ROUTES = {"/ping": _ping, "/capture": _capture}
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
		response["data"] = json.dumps(body)
		return response

	if TOKEN == _TOKEN_PLACEHOLDER:
		return reply(503, "Service Unavailable", {"error": "bridge was not built with a token; run build_spike.py"})

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
		response["data"] = json.dumps({"error": "unhandled bridge error; see TouchDesigner's textport"})
		return response
