#!/usr/bin/env python3
"""YPK Workshop: deterministic packaging; recognition and Lua authoring belong to the agent.

Python 3.10+, Pillow for image decoding/conversion. No model API or game installation
is modified. All commands print JSON and return nonzero on a failed check.
"""
from __future__ import annotations

import argparse
import configparser
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

VERSION = "0.1.0"
MANIFEST = "ypk-workshop/manifest.json"
DATA_COLUMNS = ("id", "ot", "alias", "setcode", "type", "atk", "def", "level", "race", "attribute", "category")
TEXT_COLUMNS = ("id", "name", "desc", *(f"str{i}" for i in range(1, 17)))
DEFAULTS = {"ot": 1, "alias": 0, "setcode": 0, "category": 0}
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ID = 2**31 - 1


class YPKError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise YPKError(f"{field}: expected integer or decimal/hex integer string")
    try:
        number = value if isinstance(value, int) else int(value, 16 if value.lower().startswith(("0x", "-0x")) else 10)
    except ValueError as exc:
        raise YPKError(f"{field}: invalid integer {value!r}") from exc
    if not -(2**63) <= number < 2**63:
        raise YPKError(f"{field}: outside SQLite signed 64-bit range")
    return number


def archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or ".." in path.parts or ":" in name or "\x00" in name:
        raise YPKError(f"Unsafe or unsupported archive path: {name!r}")


def read_archive(path: Path) -> tuple[dict[str, bytes], dict[str, zipfile.ZipInfo], bytes]:
    entries, infos, folded = {}, {}, set()
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 100000 or sum(i.file_size for i in archive.infolist()) > MAX_ARCHIVE_BYTES:
                raise YPKError("Archive exceeds supported size (512 MiB / 100,000 members)")
            for info in archive.infolist():
                archive_name(info.filename)
                key = info.filename.casefold()
                if key in folded:
                    raise YPKError(f"Duplicate archive path, ignoring case: {info.filename}")
                if info.file_size > MAX_MEMBER_BYTES or info.flag_bits & 1:
                    raise YPKError(f"Oversized or encrypted member: {info.filename}")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise YPKError(f"Unsupported ZIP compression: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise YPKError(f"Symbolic link in archive: {info.filename}")
                folded.add(key)
                entries[info.filename] = archive.read(info)  # validates CRC while reading
                infos[info.filename] = info
            return entries, infos, archive.comment
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise YPKError(f"Cannot read YPK {path}: {exc}") from exc


def database_rows(data: bytes) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="ypk_db_") as folder:
        path = Path(folder) / "read.cdb"
        path.write_bytes(data)
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise YPKError("CDB integrity check failed")
            for table, expected in (("datas", DATA_COLUMNS), ("texts", TEXT_COLUMNS)):
                actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not set(expected).issubset(actual):
                    raise YPKError(f"Unsupported CDB {table} schema; missing {sorted(set(expected) - actual)}")
            data_ids = [r[0] for r in connection.execute("SELECT id FROM datas")]
            text_ids = [r[0] for r in connection.execute("SELECT id FROM texts")]
            if len(set(data_ids)) != len(data_ids) or len(set(text_ids)) != len(text_ids) or set(data_ids) != set(text_ids):
                raise YPKError("CDB has duplicate IDs or mismatched datas/texts IDs")
            return [dict(row) for row in connection.execute(
                "SELECT datas.id, datas.type, texts.name FROM datas JOIN texts USING(id) ORDER BY id"
            )]
        except sqlite3.DatabaseError as exc:
            raise YPKError(f"Invalid or unsupported CDB: {exc}") from exc
        finally:
            connection.close()


def inventory(entries: dict[str, bytes]) -> dict[int, dict]:
    cards = {}
    databases = [name for name in entries if name.lower().endswith(".cdb")]
    if not databases:
        raise YPKError("YPK contains no CDB database")
    for name in databases:
        for card in database_rows(entries[name]):
            card_id = card["id"]
            if card_id in cards:
                raise YPKError(f"ID {card_id} occurs in multiple CDBs: {cards[card_id]['database']} and {name}")
            card["database"] = name
            card["script"] = f"script/c{card_id}.lua" in entries
            card["picture"] = any(f"pics/{card_id}.{ext}" in entries for ext in ("jpg", "png"))
            cards[card_id] = card
    return cards


def project_path(root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise YPKError(f"{field}: missing project-relative file path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise YPKError(f"{field}: file must be inside the card project")
    if not path.is_file():
        raise YPKError(f"{field}: file not found: {path}")
    return path


def load_project(path: Path) -> dict:
    if path.is_dir():
        path = path / "project.json"
    try:
        project = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise YPKError(f"Invalid project JSON: {exc}") from exc
    if not isinstance(project, dict) or project.get("schema_version") != 1:
        raise YPKError("project.json must have schema_version: 1")
    package = project.get("package", {})
    if not isinstance(package, dict) or not isinstance(package.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", package["name"]):
        raise YPKError("package.name must be a lowercase ASCII slug (1–64 characters)")
    for field in ("display_name", "author", "homepage"):
        if field in package and (not isinstance(package[field], str) or "\n" in package[field] or "\r" in package[field]):
            raise YPKError(f"package.{field}: expected single-line string")
    target = project.get("target", {})
    if not isinstance(target, dict) or target.get("family") != "ygopro-fluorohydride":
        raise YPKError("This version targets ygopro-fluorohydride; other engine families need an explicit adapter")
    cards = project.get("cards")
    if not isinstance(cards, list) or not cards:
        raise YPKError("project.cards must be a nonempty list of fully prepared cards")
    seen = set()
    for card in cards:
        if not isinstance(card, dict):
            raise YPKError("Each project.cards item must be an object")
        for field in DATA_COLUMNS:
            if field not in card and field not in DEFAULTS:
                raise YPKError(f"Card missing required data field {field}")
            card[field] = integer(card.get(field, DEFAULTS.get(field)), field)
        card_id = card["id"]
        if not 1 <= card_id <= MAX_ID or card_id in seen:
            raise YPKError(f"Invalid or duplicate project card ID: {card_id}")
        seen.add(card_id)
        for field in ("name", "desc"):
            if not isinstance(card.get(field), str) or not card[field].strip():
                raise YPKError(f"Card {card_id}: missing {field}")
        strings = card.setdefault("strings", [])
        if not isinstance(strings, list) or len(strings) > 16 or any(not isinstance(s, str) for s in strings):
            raise YPKError(f"Card {card_id}: strings must contain at most 16 strings")
        source = card.get("source", {})
        if not isinstance(source, dict) or not isinstance(source.get("original_text"), str) or not source["original_text"].strip():
            raise YPKError(f"Card {card_id}: preserve source.original_text before building")
        if card.get("unresolved") != []:
            raise YPKError(f"Card {card_id}: unresolved must explicitly be [] after textual uncertainties are resolved")
        try:
            script = project_path(path.parent, card.get("script"), f"card {card_id} script").read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            raise YPKError(f"Card {card_id}: Lua script must be UTF-8") from exc
        if not script.strip():
            raise YPKError(f"Card {card_id}: empty Lua script")
        image = card.get("image", {})
        if not isinstance(image, dict):
            raise YPKError(f"Card {card_id}: image must be an object")
        project_path(path.parent, image.get("path"), f"card {card_id} image")
    project["_root"] = path.parent.resolve()
    return project


def render_image(path: Path, crop: list[int] | None = None, max_edge: int | None = None) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise YPKError("Image handling requires Pillow: python -m pip install -r requirements.txt") from exc
    try:
        with Image.open(path) as original:
            original.load()
            exif_orientation = original.getexif().get(274, 1)
            if original.format == "JPEG" and exif_orientation == 1 and original.mode == "RGB" and crop is None and max_edge is None:
                return path.read_bytes()
            image = ImageOps.exif_transpose(original)
            if crop is not None:
                if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(type(v) is not int for v in crop):
                    raise YPKError("crop must be four integers: left, top, right, bottom (after EXIF rotation)")
                left, top, right, bottom = crop
                if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                    raise YPKError(f"crop is outside image dimensions {image.size}: {crop}")
                image = image.crop((left, top, right, bottom))
            if max_edge is not None:
                if type(max_edge) is not int or max_edge <= 0:
                    raise YPKError("max_edge must be a positive integer")
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            # Flatten transparency on white instead of producing dark card borders.
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                image = background.convert("RGB")
            else:
                image = image.convert("RGB")
            result = io.BytesIO()
            image.save(result, format="JPEG", quality=95, subsampling=0)
            return result.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise YPKError(f"Cannot decode image {path}: {exc}") from exc


def make_database(cards: list[dict]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="ypk_create_") as folder:
        path = Path(folder) / "new.cdb"
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY," + ",".join(f"{c} INTEGER" for c in DATA_COLUMNS[1:]) + ")")
            connection.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY," + ",".join(f"{c} TEXT" for c in TEXT_COLUMNS[1:]) + ")")
            for card in cards:
                connection.execute("INSERT INTO datas VALUES (" + ",".join("?" for _ in DATA_COLUMNS) + ")", [card[c] for c in DATA_COLUMNS])
                strings = card["strings"] + [""] * (16 - len(card["strings"]))
                connection.execute("INSERT INTO texts VALUES (" + ",".join("?" for _ in TEXT_COLUMNS) + ")", [card["id"], card["name"], card["desc"], *strings])
            connection.commit()
        finally:
            connection.close()
        return path.read_bytes()


def lua_syntax(entries: dict[str, bytes], names: list[str], executable: str | None) -> dict:
    if not executable:
        return {"status": "not_checked", "reason": "No luac supplied; packaging does not prove Lua syntax or effects"}
    binary = shutil.which(executable)
    if binary is None:
        raise YPKError(f"Lua compiler not found: {executable}")
    with tempfile.TemporaryDirectory(prefix="ypk_lua_") as folder:
        for index, name in enumerate(names):
            path = Path(folder) / f"card-{index}.lua"
            path.write_bytes(entries[name])
            result = subprocess.run([binary, "-p", str(path)], capture_output=True, timeout=15)
            if result.returncode != 0:
                raise YPKError(f"Lua syntax check failed for {name}: " + result.stderr.decode("utf-8", errors="replace")[:4000])
    return {"status": "passed", "scripts": names, "compiler": binary}


def engine_inventory(root: Path) -> tuple[dict[int, list[str]], set[str]]:
    root = root.resolve()
    if not root.is_dir() or not (root / "cards.cdb").is_file():
        raise YPKError(f"Expected YGOPro directory with cards.cdb: {root}")
    ids, cdb_names = {}, set()

    def collect(data: bytes, where: str) -> None:
        for card in database_rows(data):
            ids.setdefault(card["id"], []).append(where)

    for database in sorted([*root.glob("*.cdb"), *(root / "expansions").rglob("*.cdb")]):
        cdb_names.add(database.name.casefold())
        collect(database.read_bytes(), str(database))
    for archive in sorted((root / "expansions").rglob("*.ypk")):
        entries, _, _ = read_archive(archive)
        for name, data in entries.items():
            if name.lower().endswith(".cdb"):
                cdb_names.add(PurePosixPath(name).name.casefold())
                collect(data, f"{archive}!/{name}")
            match = re.fullmatch(r"script/c(\d+)\.lua", name)
            if match:
                ids.setdefault(int(match[1]), []).append(f"{archive}!/{name}")
    for script in (root / "script").glob("c*.lua"):
        match = re.fullmatch(r"c(\d+)\.lua", script.name)
        if match:
            ids.setdefault(int(match[1]), []).append(str(script))
    return ids, cdb_names


def check_entries(entries: dict[str, bytes]) -> dict:
    cards = inventory(entries)
    managed = None
    if MANIFEST in entries:
        try:
            managed = json.loads(entries[MANIFEST].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise YPKError("Invalid Workshop manifest") from exc
        if managed.get("schema_version") != 1 or not isinstance(managed.get("files"), dict):
            raise YPKError("Unsupported Workshop manifest")
        expected_files = set(entries) - {MANIFEST}
        if set(managed["files"]) != expected_files:
            raise YPKError("Workshop hash manifest does not cover exactly the archive members")
        for name, expected in managed["files"].items():
            if digest(entries[name]) != expected:
                raise YPKError(f"Manifest hash mismatch: {name}")
        if set(cards) != {c["id"] for c in managed.get("cards", [])}:
            raise YPKError("Workshop card list differs from CDB IDs")
        for card in managed["cards"]:
            if card.get("managed"):
                card_id = card["id"]
                if not cards[card_id]["script"] or not cards[card_id]["picture"]:
                    raise YPKError(f"Managed card {card_id} is missing Lua or picture")
                picture = entries[f"pics/{card_id}.jpg"]
                if not picture.startswith(b"\xff\xd8") or not picture.endswith(b"\xff\xd9"):
                    raise YPKError(f"Invalid JPEG header/trailer: {card_id}")
                try:
                    from PIL import Image
                except ImportError:
                    pass
                else:
                    try:
                        with Image.open(io.BytesIO(picture)) as image:
                            if image.format != "JPEG":
                                raise YPKError(f"Managed picture is not JPEG: {card_id}")
                            image.verify()
                    except OSError as exc:
                        raise YPKError(f"Invalid JPEG: {card_id}: {exc}") from exc
    return {
        "package_structure": "passed",
        "card_count": len(cards),
        "databases": sorted(n for n in entries if n.lower().endswith(".cdb")),
        "cards": sorted(cards.values(), key=lambda c: c["id"]),
        "workshop_manifest": managed is not None,
        "effect_runtime": "not_verified",
        "note": "ZIP/CDB/assets/hash checks do not verify game behavior",
    }


def metadata(project: dict, filename: str) -> bytes:
    package = project["package"]
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config["YGOProExpansionPack"] = {
        "FileName": filename,
        "PackName": package.get("display_name", package["name"]),
        "PackAuthor": package.get("author", "Local creator"),
        "PackHomePage": package.get("homepage", ""),
    }
    stream = io.StringIO()
    config.write(stream)
    return stream.getvalue().encode("utf-8")


def atomic_bytes(output: Path, data: bytes) -> None:
    output = output.resolve()
    if output.exists():
        raise YPKError(f"Output exists; choose a new filename: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation is deliberate: source archives and existing outputs are never overwritten.
    with output.open("xb") as stream:
        stream.write(data)


def build(project_path_arg: Path, output: Path, base: Path | None = None, engine_root: Path | None = None, luac: str | None = None) -> dict:
    project = load_project(project_path_arg)
    output = output.resolve()
    if output.suffix.lower() != ".ypk":
        raise YPKError("Output must have .ypk extension")
    if output.exists():
        raise YPKError(f"Output exists; choose a new filename: {output}")
    if base and output == base.resolve():
        raise YPKError("Output must differ from source YPK")
    entries, infos, archive_comment = read_archive(base) if base else ({}, {}, b"")
    original_entries = dict(entries)
    base_cards = inventory(entries) if base else {}
    if base:
        check_entries(entries)
    new_ids = {card["id"] for card in project["cards"]}
    duplicates = new_ids & set(base_cards)
    if duplicates:
        raise YPKError(f"Cards already exist in base YPK: {sorted(duplicates)}. This append command never replaces existing cards")
    external_ids, external_cdb_names = engine_inventory(engine_root) if engine_root else ({}, set())
    clashes = {str(cid): external_ids[cid] for cid in sorted(new_ids & set(external_ids))}
    if clashes:
        raise YPKError("Card IDs conflict with target installation: " + json.dumps(clashes, ensure_ascii=False))

    additions, scripts = {}, []
    for card in project["cards"]:
        card_id = card["id"]
        script_name = f"script/c{card_id}.lua"
        scripts.append(script_name)
        additions[script_name] = project_path(project["_root"], card["script"], "script").read_bytes()
        image = card["image"]
        additions[f"pics/{card_id}.jpg"] = render_image(project_path(project["_root"], image["path"], "image"), image.get("crop"), image.get("max_edge"))
    db_data = make_database(project["cards"])
    token = digest(db_data + json_bytes({name: digest(value) for name, value in sorted(additions.items())}))[:12]
    database_name = f"ypkw-{project['package']['name']}-{token}.cdb"
    if database_name.casefold() in external_cdb_names:
        raise YPKError(f"CDB filename already loaded by target installation: {database_name}")
    additions[database_name] = db_data
    for name in additions:
        if name.casefold() in {existing.casefold() for existing in entries}:
            raise YPKError(f"Archive asset already exists: {name}")
    entries.update(additions)
    if not base:
        entries["corres_srv.ini"] = metadata(project, output.name)
    # Preserve existing pack metadata; Workshop metadata records the current output filename.
    syntax = lua_syntax(entries, scripts, luac)
    prior = json.loads(original_entries[MANIFEST]) if MANIFEST in original_entries else {}
    prior_cards = {c["id"]: c for c in prior.get("cards", [])}
    manifest_cards = []
    for cid, info in sorted(base_cards.items()):
        manifest_cards.append(prior_cards.get(cid, {"id": cid, "name": info["name"], "managed": False}))
    for card in project["cards"]:
        manifest_cards.append({k: v for k, v in card.items()} | {"managed": True})
    manifest = {
        "schema_version": 1,
        "tool_version": VERSION,
        "output_filename": output.name,
        "package": project["package"],
        "target": project["target"],
        "cards": sorted(manifest_cards, key=lambda c: c["id"]),
        "validation": {"package_structure": "passed", "new_card_lua_syntax": syntax, "effect_runtime": "not_verified", "engine_collisions": "passed" if engine_root else "not_checked"},
        "base_sha256": digest(base.read_bytes()) if base else None,
        "history": prior.get("history", []) + [{"operation": "append" if base else "create", "added_ids": sorted(new_ids)}],
        "files": {name: digest(data) for name, data in sorted(entries.items()) if name != MANIFEST},
    }
    entries[MANIFEST] = json_bytes(manifest)
    report = check_entries(entries)
    report.update({"operation": "append" if base else "create", "added_ids": sorted(new_ids), "lua_syntax": syntax, "engine_collisions": "passed" if engine_root else "not_checked"})
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".ypk-", suffix=".tmp", dir=output.parent)
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = archive_comment
            for name, data in sorted(entries.items()):
                info = copy.copy(infos[name]) if name in infos else zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                if name not in infos:
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        verified, _, _ = read_archive(temporary_path)
        check_entries(verified)
        if any(verified[name] != data for name, data in original_entries.items() if name != MANIFEST):
            raise YPKError("Append verification failed: existing archive members changed")
        # Reserve output exclusively, then atomically replace only our newly reserved file.
        with output.open("xb"):
            pass
        try:
            os.replace(temporary_path, output)
        except OSError:
            output.unlink(missing_ok=True)
            raise
    finally:
        temporary_path.unlink(missing_ok=True)
    report.update({"output": str(output), "sha256": digest(output.read_bytes()), "preserved_base_members": len(original_entries) - int(MANIFEST in original_entries)})
    return report


def main(argv: list[str] | None = None) -> int:
    # Stable UTF-8 JSON on Windows, including when stdout is a pipe.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create an empty card project (agent fills project.json, scripts and images)")
    init.add_argument("--project", type=Path, required=True)
    init.add_argument("--name", required=True)
    init.add_argument("--source-url", default="")
    assemble = commands.add_parser("build", help="Build a new YPK, or append prepared cards using --base")
    assemble.add_argument("--project", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    assemble.add_argument("--base", type=Path)
    assemble.add_argument("--engine-root", type=Path)
    assemble.add_argument("--luac", help="Lua compiler matching the target engine; runs syntax check only")
    for command in ("inspect", "validate"):
        inspect = commands.add_parser(command, help="Inspect CDB/assets and validate CRC/hash manifest")
        inspect.add_argument("package", type=Path)
        if command == "validate":
            inspect.add_argument("--luac")
    image = commands.add_parser("image", help="Crop/convert local source image to JPEG; never redraws the card")
    image.add_argument("source", type=Path)
    image.add_argument("--output", type=Path, required=True)
    image.add_argument("--crop", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    image.add_argument("--max-edge", type=int)
    ids = commands.add_parser("ids", help="Suggest temporary IDs unused in a supplied engine/base package")
    ids.add_argument("--engine-root", type=Path)
    ids.add_argument("--base", type=Path)
    ids.add_argument("--start", type=int, default=260000000)
    ids.add_argument("--count", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.name):
                raise YPKError("name must be a lowercase ASCII slug")
            root = args.project.resolve()
            if root.exists():
                raise YPKError(f"Project directory already exists: {root}")
            root.mkdir(parents=True)
            (root / "script").mkdir()
            (root / "images").mkdir()
            template = {"schema_version": 1, "package": {"name": args.name, "display_name": args.name, "author": "Local creator", "homepage": args.source_url}, "target": {"family": "ygopro-fluorohydride", "script_api": "Inspect target engine before authoring Lua"}, "cards": []}
            (root / "project.json").write_bytes(json_bytes(template))
            result = {"project": str(root / "project.json"), "status": "empty_project", "next": "Agent extracts card text, writes translations and Lua, and supplies local images"}
        elif args.command == "build":
            result = build(args.project, args.output, args.base, args.engine_root, args.luac)
        elif args.command in ("inspect", "validate"):
            entries, _, _ = read_archive(args.package)
            result = check_entries(entries)
            if args.command == "validate":
                result["lua_syntax"] = lua_syntax(entries, sorted(n for n in entries if re.fullmatch(r"script/c\d+\.lua", n)), args.luac)
        elif args.command == "image":
            if args.output.suffix.lower() not in (".jpg", ".jpeg"):
                raise YPKError("Image output must have .jpg or .jpeg extension")
            data = render_image(args.source, args.crop, args.max_edge)
            atomic_bytes(args.output, data)
            result = {"output": str(args.output.resolve()), "sha256": digest(data), "format": "JPEG"}
        else:
            if not 1 <= args.count <= 1000 or not 1 <= args.start <= MAX_ID:
                raise YPKError("count must be 1–1000; start must be a positive signed-32-bit card ID")
            existing, _ = engine_inventory(args.engine_root) if args.engine_root else ({}, set())
            occupied = set(existing)
            if args.base:
                entries, _, _ = read_archive(args.base)
                occupied.update(inventory(entries))
                occupied.update(int(match[1]) for name in entries if (match := re.fullmatch(r"script/c(\d+)\.lua", name)))
            suggested, candidate = [], args.start
            while len(suggested) < args.count and candidate <= MAX_ID:
                if candidate not in occupied:
                    suggested.append(candidate)
                candidate += 1
            if len(suggested) != args.count:
                raise YPKError("No sufficient temporary IDs in selected range")
            result = {"temporary_ids": suggested, "scope": {"engine": str(args.engine_root) if args.engine_root else None, "base": str(args.base) if args.base else None}, "note": "Local unused IDs only; this does not establish official passwords or global uniqueness"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (YPKError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
