"""/describe-type, run from the real bridge source against stand-ins for TouchDesigner.

The fakes model what research/PHASE3_KNOWLEDGE_RESULTS.md recorded on a live build. They test
our logic; TouchDesigner's own behavior is covered by tests/integration/test_knowledge.py.
"""

import fnmatch
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (Path(__file__).parents[2] / "bridge" / "src" / "callbacks.py").read_text(encoding="utf-8")
TOKEN = "test-token-123"
SCRATCH = "td_mcp_describe"


def par(name, style="Float", page: str | None = "Main", **extra):
    values = {
        "name": name,
        "label": name.title(),
        "style": style,
        "page": SimpleNamespace(name=page) if page else None,
        "default": 0.0,
        "defaultMode": "ParMode.CONSTANT",
        "defaultExpr": "",
        "min": 0.0,
        "max": 1.0,
        "clampMin": False,
        "clampMax": False,
        "normMin": 0.0,
        "normMax": 1.0,
        "menuNames": None,
        "menuLabels": None,
        "help": "",
        "readOnly": False,
        "isCustom": False,
        "isNumber": style in ("Float", "Int"),
        "isMenu": style == "Menu",
        "isString": style in ("Str", "StrMenu"),
        "sequence": None,
    }
    values.update(extra)
    return SimpleNamespace(**values)


NOISE_PARS = [
    par("pageindex", "Int", page=None),
    par(
        "type", "Menu", "Noise", default="simplex3d", menuNames=["perlin2d", "simplex3d"],
        menuLabels=["Perlin 2D", "Simplex 3D"], help="The noise function. " + "x" * 400,
    ),
    par("seed", "Float", "Noise", default=1.0, normMin=1.0, normMax=9999.0),
    par("resolutionw", "Int", "Common", default=256, min=1.0, clampMin=True,
        menuNames=["640 480"], help="Help Not Available."),
    par("resmult", "Toggle", "Common", default=True, menuNames=["off", "on"]),
    par("snap", "Pulse", "Common", default=False),
    par("label", "Str", "Common", default="", defaultMode="ParMode.EXPRESSION",
        defaultExpr="me.name", readOnly=True),
]


class Scratch:
    """A COMP that records the order of the things done to it."""

    def __init__(self, world, name):
        self.world, self.name, self.path = world, name, "/bridge/" + name
        self.kids: list = []
        self._cooking = True

    @property
    def allowCooking(self):
        return self._cooking

    @allowCooking.setter
    def allowCooking(self, value):
        self.world.log.append(("allowCooking", value))
        self._cooking = value

    def create(self, optype):
        self.world.log.append(("create", optype, self._cooking))
        if optype in self.world.refuses:
            raise RuntimeError("not licensed")
        made = SimpleNamespace(
            path=self.path + "/made", label="Noise", type=optype[:-3], family=optype[-3:],
            minInputs=0, maxInputs=2, builtinPars=self.world.pars, customPars=self.world.custom,
        )
        made.pars = lambda pattern: [
            p for p in made.builtinPars + made.customPars
            if any(fnmatch.fnmatch(p.name, one) for one in pattern.split())
        ]
        self.kids = [made]
        if optype == "glslTOP":
            self.kids.append(SimpleNamespace(path=self.path + "/pixel", type="text", family="DAT"))
        return made

    def findChildren(self, depth=None, includeUtility=False):
        return list(self.kids)

    def destroy(self):
        self.world.log.append(("destroy", self.name))
        del self.world.children[self.name]


class World:
    def __init__(self, config_folder="/nowhere"):
        self.log: list = []
        self.children: dict = {}
        self.pars: list = NOISE_PARS
        self.custom: list = []
        self.refuses: set = set()
        names = {
            "TOP": ["noiseTOP", "glslTOP", "videodeviceinTOP", "zedTOP", "nuitrackTOP"],
            "CHOP": ["laserCHOP", "laserdeviceCHOP", "audiodeviceoutCHOP", "serialCHOP"],
            "DAT": ["videodevicesDAT", "webserverDAT"],
        }
        families = {
            family: [type(name, (), {}) for name in members] for family, members in names.items()
        }
        box = SimpleNamespace(path="/bridge", op=self.children.get, create=self._create)
        source = SOURCE.replace("__TOKEN__", TOKEN).replace("__TEST_FIXTURES__", "0")
        self.ns = {
            "op": lambda path: None,
            "parent": lambda: box,
            "app": SimpleNamespace(build="1", version="2", configFolder=config_folder),
            "project": SimpleNamespace(),
            "families": families,
        }
        exec(compile(source, "callbacks.py", "exec"), self.ns)  # noqa: S102

    def _create(self, optype, name):
        assert optype == "baseCOMP"
        assert name not in self.children, "a stale scratch must be removed first"
        self.log.append(("scratch", name))
        self.children[name] = Scratch(self, name)
        return self.children[name]

    def describe(self, **pars):
        request = {
            "uri": {"path": "/describe-type"}, "pars": pars, "method": "GET",
            "Host": "127.0.0.1:9991", "Authorization": "Bearer " + TOKEN,
        }
        response: dict = {}
        self.ns["onHTTPRequest"](None, request, response)
        assert response["statusCode"] == 200, response["data"]
        return json.loads(response["data"])["result"]


def names(result):
    return [p["name"] for p in result["parameters"]]


def test_a_live_type_is_described_and_nothing_is_left_behind():
    world = World()
    result = world.describe(type="noiseTOP")
    assert (result["source"], result["note"]) == ("live", None)
    assert (result["family"], result["label"]) == ("TOP", "Noise")
    assert (result["min_inputs"], result["max_inputs"]) == (0, 2)
    assert result["pages"] == ["Noise", "Common"]
    assert result["also_creates"] == []
    assert names(result) == ["type", "seed", "resolutionw", "resmult", "snap", "label"]
    assert result["parameter_count"] == 6 and result["next_offset"] is None
    assert world.children == {}


def test_cooking_is_off_before_the_operator_exists_and_the_scratch_is_destroyed_after():
    world = World()
    world.describe(type="noiseTOP")
    assert world.log == [
        ("scratch", SCRATCH),
        ("allowCooking", False),
        ("create", "noiseTOP", False),
        ("destroy", SCRATCH),
    ]


def test_a_scratch_left_by_an_unfinished_request_is_removed_first():
    world = World()
    world.children[SCRATCH] = Scratch(world, SCRATCH)
    assert world.describe(type="noiseTOP")["source"] == "live"
    assert world.log[0] == ("destroy", SCRATCH)
    assert world.children == {}


def test_a_type_touchdesigner_cannot_create_is_reported_and_cleaned_up():
    world = World()
    world.refuses.add("noiseTOP")
    assert "not licensed" in world.describe(type="noiseTOP")["error"]
    assert world.children == {}


def test_the_scratch_is_destroyed_when_reading_a_parameter_blows_up():
    class Broken:
        @property
        def page(self):
            raise RuntimeError("boom")

    world = World()
    world.pars = [Broken()]
    request = {
        "uri": {"path": "/describe-type"}, "pars": {"type": "noiseTOP"}, "Host": "127.0.0.1:1",
        "Authorization": "Bearer " + TOKEN,
    }
    response: dict = {}
    world.ns["onHTTPRequest"](None, request, response)
    assert response["statusCode"] == 500
    assert world.log[-1] == ("destroy", SCRATCH)
    assert world.children == {}


def test_each_parameter_carries_only_the_fields_that_apply():
    rows = {p["name"]: p for p in World().describe(type="noiseTOP")["parameters"]}
    assert rows["type"]["menu_names"] == ["perlin2d", "simplex3d"]
    assert rows["type"]["default"] == "simplex3d"
    assert "menu_labels" not in rows["type"] and "min" not in rows["type"]
    assert "slider_range" not in rows["type"]
    assert rows["seed"]["slider_range"] == [1.0, 9999.0]
    assert "min" not in rows["seed"] and "max" not in rows["seed"]
    # Clamped on one side only; number presets are not menus; filler help is dropped.
    assert rows["resolutionw"]["min"] == 1.0 and "max" not in rows["resolutionw"]
    assert "menu_names" not in rows["resolutionw"] and "help" not in rows["resolutionw"]
    assert "menu_names" not in rows["resmult"] and rows["resmult"]["default"] is True
    assert "default" not in rows["snap"]
    assert rows["label"]["default"] == "" and rows["label"]["default_expr"] == "me.name"
    assert rows["label"]["read_only"] is True and "custom" not in rows["label"]


def test_help_is_shortened_unless_filtering_by_name():
    world = World()
    brief = world.describe(type="noiseTOP")["parameters"][0]["help"]
    assert len(brief) == 200 and brief.endswith("...")
    full = world.describe(type="noiseTOP", name="type")
    assert names(full) == ["type"]
    assert len(full["parameters"][0]["help"]) > 400
    assert full["parameters"][0]["menu_labels"] == ["Perlin 2D", "Simplex 3D"]


def test_name_and_page_filters_and_paging():
    world = World()
    assert names(world.describe(type="noiseTOP", name="res* seed")) == [
        "seed", "resolutionw", "resmult",
    ]
    assert names(world.describe(type="noiseTOP", page="noise")) == ["type", "seed"]
    first = world.describe(type="noiseTOP", limit="4")
    assert (len(first["parameters"]), first["next_offset"], first["parameter_count"]) == (4, 4, 6)
    rest = world.describe(type="noiseTOP", offset="4", limit="4")
    assert names(rest) == ["snap", "label"] and rest["next_offset"] is None
    assert world.describe(type="noiseTOP", limit="9999")["limit"] == 250


def test_an_unknown_page_lists_the_real_ones():
    error = World().describe(type="noiseTOP", page="Nope")["error"]
    assert "Noise, Common" in error


def test_custom_parameters_are_included_and_marked():
    world = World()
    world.custom = [par("Speed", page="Mine", isCustom=True)]
    result = world.describe(type="noiseTOP", page="Mine")
    assert result["parameters"][0]["custom"] is True


def test_operators_added_alongside_are_listed():
    assert World().describe(type="glslTOP")["also_creates"] == ["textDAT"]


@pytest.mark.parametrize(
    "optype, expected",
    [("noise", "noiseTOP"), ("nosieTOP", "noiseTOP"), ("", "non-empty"), (None, "non-empty")],
)
def test_bad_types_are_refused_with_help(optype, expected):
    world = World()
    pars = {} if optype is None else {"type": optype}
    assert expected in world.describe(**pars)["error"]
    assert world.log == []


@pytest.mark.parametrize("pars", [{"offset": "-1"}, {"limit": "0"}, {"limit": "many"}])
def test_bad_paging_is_refused_before_anything_is_created(pars):
    world = World()
    assert "error" in world.describe(type="noiseTOP", **pars)
    assert world.log == []


@pytest.mark.parametrize(
    "optype",
    ["videodeviceinTOP", "videodevicesDAT", "zedTOP", "laserdeviceCHOP", "audiodeviceoutCHOP",
     "serialCHOP", "nuitrackTOP"],
)
def test_hardware_types_are_never_created(optype, tmp_path):
    world = World(str(tmp_path))
    result = world.describe(type=optype)
    assert result["source"] == "help_file"
    assert world.log == []


def test_software_types_with_similar_names_are_created():
    for optype in ("laserCHOP", "webserverDAT"):
        assert World().describe(type=optype)["source"] == "live"


def help_folder(tmp_path):
    entry = {
        "label": "Video Device In",
        "parameters": {
            "active": {"label": "Active", "summary": "Turns capture on.", "parType": "Toggle"},
            "device": {"label": "Device", "summary": "d" * 400, "parType": "StrMenu"},
        },
    }
    text = json.dumps({"help": {"TOPs": {"videodeviceinTOP": entry}}})
    (tmp_path / "TDParameterHelp.json").write_text("﻿" + text, encoding="utf-8")
    return str(tmp_path)


def test_hardware_types_are_described_from_the_builds_help_file(tmp_path):
    world = World(help_folder(tmp_path))
    result = world.describe(type="videodeviceinTOP")
    assert result["label"] == "Video Device In"
    assert names(result) == ["active", "device"]
    assert result["parameters"][0] == {
        "name": "active", "label": "Active", "style": "Toggle", "help": "Turns capture on.",
    }
    assert len(result["parameters"][1]["help"]) == 200
    assert "no defaults" in result["note"] and "permissions" in result["note"]
    filtered = world.describe(type="videodeviceinTOP", name="dev*")
    assert names(filtered) == ["device"] and len(filtered["parameters"][0]["help"]) == 400
    assert "pages are not known" in world.describe(type="videodeviceinTOP", page="X")["error"]
    assert world.log == []


@pytest.mark.parametrize("content", [None, "not json", "{}"])
def test_a_missing_or_unreadable_help_file_says_so(tmp_path, content):
    if content is not None:
        (tmp_path / "TDParameterHelp.json").write_text(content, encoding="utf-8")
    result = World(str(tmp_path)).describe(type="videodeviceinTOP")
    assert result["parameters"] == [] and "no entry" in result["note"]
