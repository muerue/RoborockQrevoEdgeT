#!/usr/bin/env python3
"""JSON-Brücke zwischen IP-Symcon und python-roborock.

Normalbetrieb: liest genau ein JSON-Objekt von stdin und gibt genau ein JSON-Objekt
auf stdout aus. Diagnosemeldungen gehen ausschließlich auf stderr.
Ersteinrichtung: --login startet den interaktiven Roborock-E-Mail-Code-Login.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

from roborock.devices.device_manager import UserParams, create_device_manager
from roborock.devices.file_cache import load_value, store_value
from roborock.roborock_typing import RoborockCommand
from roborock.web_api import RoborockApiClient

from export_roborock_map import get_map_content, make_export, normalize_png


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def field(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


async def maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def close_quietly(obj: Any, label: str) -> None:
    if obj is None:
        return
    method = field(obj, "close")
    if callable(method):
        try:
            await maybe_await(method())
        except Exception as exc:
            log(f"Hinweis: {label} konnte nicht geschlossen werden: {type(exc).__name__}: {exc}")


def enum_name(value: Any) -> str | None:
    if value is None:
        return None
    name = field(value, "name")
    if isinstance(name, str):
        return name.lower()
    return str(value)


def enum_value(value: Any) -> Any:
    return field(value, "value", value)


def ensure_private_parent(path: Path) -> None:
    # Create only the missing target directory; never change permissions on an
    # already existing shared parent directory (for example /var/lib/symcon).
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)


async def login(session_file: Path) -> dict[str, Any]:
    """Interactive, one-time login; credentials are stored using python-roborock."""
    ensure_private_parent(session_file)
    username = input("Roborock-E-Mail-Adresse: ").strip()
    if not username:
        raise ValueError("Die E-Mail-Adresse darf nicht leer sein.")

    api = RoborockApiClient(username=username)
    try:
        log("Fordere Roborock-Anmeldecode an ...")
        await api.request_code()
        code = input("Roborock-Verifizierungscode: ").strip()
        if not code:
            raise ValueError("Der Verifizierungscode darf nicht leer sein.")
        user_data = await api.code_login(code)
        params = UserParams(username=username, user_data=user_data, base_url=await api.base_url)
        await store_value(session_file, params)
        try:
            os.chmod(session_file, 0o600)
        except OSError:
            pass
        return {"ok": True, "message": f"Anmeldedaten gespeichert: {session_file}"}
    finally:
        await close_quietly(api, "RoborockApiClient")


async def load_manager(session_file: Path, cache_file: Path, device_name: str):
    if not session_file.is_file():
        raise FileNotFoundError(
            f"Keine Roborock-Anmeldung gefunden ({session_file}). "
            "Bitte roborock_bridge.py --login einmal interaktiv ausführen."
        )
    params = await load_value(session_file)
    if params is None:
        raise RuntimeError("Die gespeicherte Roborock-Anmeldung ist leer oder ungültig. Bitte erneut anmelden.")

    # WICHTIG: In python-roborock 7.12.3 kann CacheData eine lokal definierte
    # Callback-Funktion (V1Channel.rpc_channel.<locals>.rpc_strategies_cb)
    # enthalten. pickle.dumps() kann diese Closure nicht serialisieren und
    # hinterlässt eine 0-Byte-Datei, die beim nächsten Start EOFError auslöst.
    # Daher wird hier bewusst kein FileCache verwendet. Die Geräte-API und
    # die dauerhaft gespeicherte UserParams-Sitzung funktionieren ohne ihn.
    # cache_file bleibt als CLI-Argument für bestehende IP-Symcon-Instanzen erhalten.
    manager = await create_device_manager(params)
    try:
        devices = await manager.get_devices()
        device = next((item for item in devices if item.name == device_name), None)
        if device is None:
            available = ", ".join(str(field(item, "name", "?")) for item in devices)
            raise RuntimeError(f"Gerät '{device_name}' nicht gefunden. Gefundene Geräte: {available or '(keine)'}")
        if not field(device, "v1_properties"):
            raise RuntimeError(f"Gerät '{device_name}' stellt keine V1-Properties bereit.")
        return params, manager, device
    except Exception:
        await close_quietly(manager, "DeviceManager")
        raise


async def refresh_trait(label: str, trait: Any, timeout: int = 30) -> bool:
    if trait is None:
        log(f"Trait nicht verfügbar: {label}")
        return False
    try:
        await asyncio.wait_for(maybe_await(trait.refresh()), timeout=timeout)
        log(f"Aktualisiert: {label}")
        return True
    except Exception as exc:
        log(f"Hinweis: {label}.refresh() fehlgeschlagen: {type(exc).__name__}: {exc}")
        return False


def status_snapshot(status: Any, maps: Any, map_export: dict[str, Any]) -> dict[str, Any]:
    map_data = map_export.get("map_data", {})
    state_name = field(status, "state_name") or enum_name(field(status, "state"))
    charge_name = enum_name(field(status, "charge_status"))
    is_charging = bool(field(status, "is_battery_charging", False)) or state_name == "charging" or charge_name == "charging"
    return {
        "battery": field(status, "battery"),
        "state": state_name,
        "state_code": enum_value(field(status, "state")),
        "error_code": field(status, "error_code_name") or enum_name(field(status, "error_code")),
        "clean_percent": field(status, "clean_percent"),
        "clean_area_m2": field(status, "square_meter_clean_area"),
        "clean_time": field(status, "clean_time"),
        "charging": is_charging,
        "charge_status": charge_name,
        "fan_speed": field(status, "fan_speed_name"),
        "mop_mode": field(status, "water_mode_name"),
        "mop_route": field(status, "mop_route_name"),
        "current_map": field(maps, "current_map"),
        "vacuum_position": map_data.get("vacuum_position"),
        "current_room": map_data.get("vacuum_room"),
        "current_room_name": map_data.get("vacuum_room_name"),
    }


async def refresh_and_export(device: Any, output_dir: Path) -> dict[str, Any]:
    properties = device.v1_properties
    # Status first so current_map can be refreshed before requesting map content.
    await refresh_trait("status", field(properties, "status"))
    await refresh_trait("maps", field(properties, "maps"))
    await refresh_trait("rooms", field(properties, "rooms"))
    await refresh_trait("home", field(properties, "home"))
    await refresh_trait("map_content", field(properties, "map_content"))

    maps_trait = field(properties, "maps")
    map_flag = field(maps_trait, "current_map")
    if map_flag is None:
        map_flag = field(field(properties, "status"), "current_map")
    try:
        map_flag = int(map_flag) if map_flag is not None else None
    except (TypeError, ValueError):
        map_flag = None

    map_content, source = get_map_content(properties, map_flag)
    if map_content is None:
        raise RuntimeError("Roborock hat keine Kartendaten geliefert.")
    image_content = field(map_content, "image_content")
    if image_content is None:
        direct = field(properties, "map_content")
        if field(direct, "image_content") is not None:
            map_content = direct
            image_content = field(direct, "image_content")
    png = normalize_png(image_content)
    export = make_export(device, properties, map_content, map_flag)

    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "roborock_map.png"
    json_path = output_dir / "roborock_map.json"
    image_tmp = image_path.with_suffix(".png.tmp")
    json_tmp = json_path.with_suffix(".json.tmp")
    image_tmp.write_bytes(png)
    json_tmp.write_text(json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    image_tmp.replace(image_path)
    json_tmp.replace(json_path)
    log(f"Karte exportiert aus {source}: {image_path}")

    status = field(properties, "status")
    return {
        "map_export": export,
        "map_png_base64": base64.b64encode(png).decode("ascii"),
        "status": status_snapshot(status, maps_trait, export),
    }


async def run_command(device: Any, payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    properties = device.v1_properties
    action = str(payload.get("command", "")).strip().lower()
    command_map = {
        "start": RoborockCommand.APP_START,
        "pause": RoborockCommand.APP_PAUSE,
        "stop": RoborockCommand.APP_STOP,
        "dock": RoborockCommand.APP_CHARGE,
    }
    if action in command_map:
        response = await maybe_await(properties.command.send(command_map[action]))
        return {"ok": True, "command": action, "response": str(response) if response is not None else None}

    if action == "clean_room":
        try:
            segment_id = int(payload.get("segment_id"))
            repeat = int(payload.get("repeat", 1))
        except (TypeError, ValueError):
            raise ValueError("segment_id und repeat müssen Ganzzahlen sein.")
        if segment_id < 1:
            raise ValueError("Die Segment-ID muss positiv sein.")
        if repeat < 1 or repeat > 3:
            raise ValueError("repeat muss zwischen 1 und 3 liegen.")

        # Fresh metadata and current map, so segments without a real IoT room are never controlled.
        await refresh_trait("maps", field(properties, "maps"))
        await refresh_trait("rooms", field(properties, "rooms"))
        await refresh_trait("home", field(properties, "home"))
        await refresh_trait("map_content", field(properties, "map_content"))
        maps_trait = field(properties, "maps")
        map_flag = field(maps_trait, "current_map")
        try:
            map_flag = int(map_flag) if map_flag is not None else None
        except (TypeError, ValueError):
            map_flag = None
        content, _source = get_map_content(properties, map_flag)
        if content is None:
            raise RuntimeError("Raumreinigung abgebrochen: aktuelle Karte nicht verfügbar.")
        current_export = make_export(device, properties, content, map_flag)
        room = next(
            (r for r in current_export.get("map_data", {}).get("rooms", []) if int(r.get("segment_id", -1)) == segment_id),
            None,
        )
        if room is None:
            raise ValueError(f"Segment {segment_id} existiert nicht in der aktuellen Karte.")
        if not room.get("controllable") or not room.get("iot_id"):
            raise ValueError(f"Segment {segment_id} ist nicht zugeordnet und darf nicht gereinigt werden.")

        # python-roborock CommandTrait expects a list of parameter objects; Roborock's
        # app_segment_clean payload uses a dict with a list of segments and repeat count.
        response = await maybe_await(
            properties.command.send(
                RoborockCommand.APP_SEGMENT_CLEAN,
                [{"segments": [segment_id], "repeat": repeat}],
            )
        )
        return {
            "ok": True,
            "command": action,
            "segment_id": segment_id,
            "room_name": room.get("name"),
            "response": str(response) if response is not None else None,
        }

    raise ValueError(f"Nicht unterstützter Befehl: {action!r}")


async def execute(args: argparse.Namespace, payload: dict[str, Any] | None) -> dict[str, Any]:
    if args.login:
        return await login(Path(args.session_file))
    if payload is None:
        raise ValueError("Eingabe-JSON fehlt.")

    action = str(payload.get("action", "status")).strip().lower()
    params, manager, device = await load_manager(
        Path(args.session_file), Path(args.cache_file), args.device_name
    )
    try:
        if action == "status":
            result = await refresh_and_export(device, Path(args.export_dir))
            return {"ok": True, "device": device.name, **result}
        if action == "command":
            result = await run_command(device, payload, Path(args.export_dir))
            return {"ok": True, "device": device.name, **result}
        raise ValueError(f"Nicht unterstützte Aktion: {action!r}")
    finally:
        await close_quietly(device, "RoborockDevice")
        await close_quietly(manager, "DeviceManager")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--cache-file", required=True, help="Kompatibilitätsoption für bestehende IP-Symcon-Instanzen; wird aktuell nicht verwendet")
    parser.add_argument("--device-name", default="Roborock Qrevo EdgeT")
    parser.add_argument("--export-dir", default="/root/roborock_export")
    parser.add_argument("--login", action="store_true", help="Interaktive Ersteinrichtung der Roborock-Anmeldung")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload: dict[str, Any] | None = None
    try:
        if not args.login:
            try:
                payload = json.load(sys.stdin)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Eingabe ist kein gültiges JSON: {exc}") from exc
        result = asyncio.run(execute(args, payload))
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Abbruch durch Benutzer."}, ensure_ascii=False))
        return 130
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        log(f"FEHLER: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
