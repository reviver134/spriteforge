"""What a game's code contains, read off the files by code (no model call).

The game's OKF record describes every code file so later agents (level and menu designers)
know what exists without reading the code: its classes and scenes, functions, the `sf.event`
names it emits, the `sf.state` keys it sets, the input it reads, the asset ids it uses, the
`sf.choose` menus it opens and its size. These are regular-expression reads of plain browser
scripts, so they are a summary, not a parser: a name built at run time is not seen.
"""

import re
from dataclasses import dataclass, field

from .codegen import ASSET_USE, OWN_TEXTURE, strip_comments

CLASS = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)(?:\s+extends\s+([\w$.]+))?")
SCENE_KEY = re.compile(r"\bsuper\(\s*['\"]([\w-]+)['\"]")
SCENE_CONFIG = re.compile(r"\bsuper\(\s*\{[^}]*?\bkey\s*:\s*['\"]([\w-]+)['\"]")
FUNCTION = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(")
ARROW = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^()]*\)|[\w$]+)\s*=>")
# A method: a name and its parameters at the start of a line or after `{`, `}` or `;`, then
# `{` (keywords like `if (...) {` are left out below).
METHOD = re.compile(
    r"(?m)(?:^|[{};])\s*(?:async\s+|static\s+)?([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*\{"
)
KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "with", "else", "do"}
EVENT_CALL = re.compile(r"\bsf\.event\(\s*([^,()]+?)\s*[,)]")
STRING = re.compile(r"['\"]([a-z][a-z0-9_]{0,39})['\"]")
STATE_OBJECT = re.compile(r"\bsf\.state\s*=\s*\{")
STATE_FIELD = re.compile(r"\bsf\.state\.([A-Za-z_]\w*)\s*=(?!=)")
STATE_BRACKET = re.compile(r"\bsf\.state\[\s*['\"]([A-Za-z_]\w*)['\"]\s*\]\s*=(?!=)")
ADD_KEY = re.compile(r"\baddKey\(\s*['\"]([A-Za-z0-9_]+)['\"]")
ADD_KEYS = re.compile(r"\baddKeys\(\s*['\"]([A-Za-z0-9_,\s]+)['\"]")
KEY_CODE = re.compile(r"KeyCodes\.([A-Z0-9_]+)")
KEY_EVENT = re.compile(r"\bkeyboard\.on\(\s*['\"]keydown-([A-Za-z0-9_]+)['\"]")
HAS = re.compile(r"\bsf\.has\(\s*['\"]([a-z0-9_]+)['\"]")
CHOOSE = re.compile(r"\bsf\.choose\(\s*[^,]+,\s*(['\"`])(.{1,60}?)\1")
FLAG = re.compile(r"\bsf\.flags\.([A-Za-z_]\w*)")


@dataclass
class Module:
    """One code file of a build, as the game's record describes it."""

    path: str
    lines: int
    classes: list[str] = field(default_factory=list)  # "Arena (Phaser.Scene, key 'arena')"
    scenes: list[str] = field(default_factory=list)  # scene keys
    functions: list[str] = field(default_factory=list)  # "spawn()", "Arena.update()"
    events: list[str] = field(default_factory=list)  # sf.event names
    state: list[str] = field(default_factory=list)  # sf.state keys it sets
    reads_input: bool = False  # sf.input(...)
    keys: list[str] = field(default_factory=list)  # keys it reads itself (ENTER, ESC, ...)
    assets: list[str] = field(default_factory=list)  # asset ids it draws or tests for
    textures: list[str] = field(default_factory=list)  # textures it makes itself
    menus: list[str] = field(default_factory=list)  # sf.choose titles
    flags: list[str] = field(default_factory=list)  # sf.flags it reads (god, ...)
    starts: bool = False  # calls sf.start (the entry point)
    autoplay: bool = False  # defines the bot policy


def ordered(items) -> list[str]:
    return list(dict.fromkeys(items))


def object_keys(code: str, start: int) -> list[str]:
    """Top-level keys of the object literal whose `{` is at `start`."""
    depth, i, keys, expect = 0, start, [], True
    while i < len(code):
        ch = code[i]
        if ch in "{[(":
            depth += 1
            if depth == 1:
                expect = True
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1 and ch == ",":
            expect = True
        elif depth == 1 and expect and not ch.isspace():
            match = re.match(r"['\"]?([A-Za-z_$][\w$]*)['\"]?\s*(:|,|})", code[i:])
            if match:
                keys.append(match[1])
                i += match.end(1) - 1
            expect = False
        elif ch in "'\"`" and depth >= 1:  # skip strings
            end = code.find(ch, i + 1)
            i = end if end > 0 else i
        i += 1
    return keys


def class_spans(code: str) -> list[tuple[str, int, int]]:
    """(class name, start, end) of each class body."""
    spans = []
    for m in CLASS.finditer(code):
        open_at = code.find("{", m.end())
        depth, i = 0, open_at
        while 0 <= i < len(code):
            depth += {"{": 1, "}": -1}.get(code[i], 0)
            if depth == 0:
                break
            i += 1
        spans.append((m[1], open_at, i))
    return spans


def read_module(path: str, content: str) -> Module:
    code = strip_comments(content)
    mod = Module(path=path, lines=content.count("\n") + (0 if content.endswith("\n") else 1))
    spans = class_spans(code)
    for m in CLASS.finditer(code):
        body = next((code[a:b] for name, a, b in spans if name == m[1]), "")
        key = SCENE_KEY.search(body) or SCENE_CONFIG.search(body)
        base = m[2] or ""
        if base.endswith("Scene"):
            mod.scenes.append(key[1] if key else m[1])
        detail = ", ".join(p for p in (base, f"key '{key[1]}'" if key and base else "") if p)
        mod.classes.append(f"{m[1]} ({detail})" if detail else m[1])
    owner = lambda pos: next((name for name, a, b in spans if a < pos < b), "")
    for m in METHOD.finditer(code):
        if m[1] in KEYWORDS or not (cls := owner(m.start(1))):
            continue
        mod.functions.append(f"{cls}.{m[1]}()")
    for regex in (FUNCTION, ARROW):
        for m in regex.finditer(code):
            if not owner(m.start()):
                mod.functions.append(f"{m[1]}()")
    for m in EVENT_CALL.finditer(code):
        mod.events += STRING.findall(m[1])
    for m in STATE_OBJECT.finditer(code):
        mod.state += object_keys(code, m.end() - 1)
    mod.state += STATE_FIELD.findall(code) + STATE_BRACKET.findall(code)
    mod.reads_input = bool(re.search(r"\bsf\.input\(", code))
    keys = ADD_KEY.findall(code) + KEY_CODE.findall(code) + KEY_EVENT.findall(code)
    for group in ADD_KEYS.findall(code):
        keys += [k.strip() for k in group.split(",") if k.strip()]
    mod.keys = ordered(k.upper() for k in keys)
    mod.textures = ordered(OWN_TEXTURE.findall(code))
    mod.assets = ordered(a for a in ASSET_USE.findall(code) + HAS.findall(code)
                         if a not in mod.textures)  # fmt: skip
    mod.menus = ordered(m[2] for m in CHOOSE.finditer(code))
    mod.flags = ordered(FLAG.findall(code))
    mod.starts = bool(re.search(r"\bsf\.start\(", code))
    mod.autoplay = bool(re.search(r"\bautoplay\b", code))
    for name in ("classes", "scenes", "functions", "events", "state"):
        setattr(mod, name, ordered(getattr(mod, name)))
    return mod


def read_code(files: dict[str, str]) -> list[Module]:
    """Every file of a build, in load order (helpers first, game.js last)."""
    order = [p for p in files if p != "game.js"] + [p for p in files if p == "game.js"]
    return [read_module(p, files[p]) for p in order]
