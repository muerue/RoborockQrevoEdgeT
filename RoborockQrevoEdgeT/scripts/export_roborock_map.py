#!/usr/bin/env python3
"""Exportiert Karte und Raummetadaten eines Roborock über python-roborock.

Standard-Zielverzeichnis: /root/roborock_export
Ausgabe:
  roborock_map.png
  roborock_map.json

Es werden keine lokalen Schlüssel oder Zugangsdaten in die Exportdateien geschrieben.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import math
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from roborock.web_api import RoborockApiClient
from roborock.devices.device_manager import UserParams, create_device_manager

DEFAULT_DEVICE_NAME = "Roborock Qrevo EdgeT"
DEFAULT_OUTPUT_DIR = Path("/root/roborock_export")
REFRESH_TIMEOUT_SECONDS = 30


def log(message: str = "") -> None:
    print(message, flush=True)


async def maybe_await(value: Any) -> Any:
    """Unterstützt sowohl synchrone als auch asynchrone API-Methoden."""
    if inspect.isawaitable(value):
        return await value
    return value


async def close_quietly(obj: Any, label: str) -> None:
    """Schließt eine Verbindung, ohne andere Aufräumschritte zu verhindern."""
    if obj is None:
        return
    close_method = getattr(obj, "close", None)
    if not callable(close_method):
        return
    try:
        await maybe_await(close_method())
        log(f"Verbindung geschlossen: {label}")
    except Exception as exc:  # Aufräumen soll keinen erfolgreich geschriebenen Export entwerten.
        log(f"Warnung beim Schließen von {label}: {type(exc).__name__}: {exc}")


async def refresh_quietly(label: str, trait: Any) -> bool:
    """Aktualisiert einen Trait mit Timeout; liefert True bei Erfolg."""
    if trait is None:
        log(f"Hinweis: {label} ist nicht verfügbar.")
        return False
    try:
        await asyncio.wait_for(trait.refresh(), timeout=REFRESH_TIMEOUT_SECONDS)
        log(f"Aktualisiert: {label}")
        return True
    except asyncio.TimeoutError:
        log(f"Warnung: Zeitüberschreitung beim Aktualisieren von {label}.")
    except Exception as exc:
        log(f"Warnung: {label} konnte nicht aktualisiert werden: {type(exc).__name__}: {exc}")
    return False


def attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def finite_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(num):
        return None
    if num.is_integer():
        return int(num)
    return num


def point_to_dict(point: Any) -> dict[str, Any] | None:
    if point is None:
        return None
    result: dict[str, Any] = {}
    for key in ("x", "y", "a"):
        value = attr(point, key)
        if value is not None:
            result[key] = finite_number(value)
    if result:
        return result
    if isinstance(point, dict):
        return {str(k): to_plain(v) for k, v in point.items()}
    return to_plain(point)


def to_plain(value: Any, depth: int = 0) -> Any:
    """Konvertiert einfache API-Datenobjekte rekursiv in JSON-kompatible Werte."""
    if depth > 12:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, Enum):
        return to_plain(value.value, depth + 1)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": type(value).__name__, "length": len(value)}
    if isinstance(value, dict):
        return {str(k): to_plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(v, depth + 1) for v in value]

    # Manche Roborock-Datenklassen stellen as_dict() bereit.
    as_dict = attr(value, "as_dict")
    if callable(as_dict):
        try:
            data = as_dict()
            if data is not value:
                return to_plain(data, depth + 1)
        except Exception:
            pass

    # Nur öffentliche Attribute übernehmen; keine privaten Verbindungs-/Schlüsselwerte.
    obj_dict = attr(value, "__dict__")
    if isinstance(obj_dict, dict):
        public = {str(k): v for k, v in obj_dict.items() if not str(k).startswith("_")}
        if public:
            return {k: to_plain(v, depth + 1) for k, v in public.items()}
    return str(value)


def calibration_to_json(map_data: Any) -> Any:
    """Gibt die Kalibrierung aus, möglichst als Liste von Vakuum-/Kartenpunkten."""
    method = attr(map_data, "calibration")
    if not callable(method):
        return None
    try:
        calibration = method()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if calibration is None:
        return None

    if isinstance(calibration, (list, tuple)):
        return to_plain(calibration)
    # In einigen Versionen ist CalibrationPoints iterierbar.
    try:
        points = list(calibration)
        if points:
            return to_plain(points)
    except (TypeError, AttributeError):
        pass
    return to_plain(calibration)


def mapping_entries(source: Any) -> list[Any]:
    """Normalisiert Räume aus Listen oder Dictionaries auf eine Liste."""
    if source is None:
        return []
    if isinstance(source, dict):
        return list(source.values())
    if isinstance(source, (list, tuple, set)):
        return list(source)
    try:
        return list(source)
    except (TypeError, ValueError):
        return []


def build_room_metadata(properties: Any, map_flag: int | None) -> dict[int, dict[str, Any]]:
    """Führt API-Raumzuordnung und Home-Map-Metadaten zusammen.

    properties.rooms enthält hier nur benannte Räume; properties.home kann zusätzlich
    Platzhalter wie Segment 3 / "Raum" / leere IoT-ID enthalten. Platzhalter bleiben
    im Export sichtbar, werden aber später als nicht steuerbar markiert.
    """
    sources: list[tuple[str, Any]] = []

    rooms_trait = attr(properties, "rooms")
    sources.append(("rooms_api", attr(rooms_trait, "rooms")))
    sources.append(("rooms_api_map", attr(rooms_trait, "room_map")))

    home = attr(properties, "home")
    sources.append(("home_current_rooms", attr(home, "current_rooms")))
    current_map_data = attr(home, "current_map_data")
    sources.append(("home_current_map_data", attr(current_map_data, "rooms")))

    home_map_info = attr(home, "home_map_info")
    if isinstance(home_map_info, dict):
        map_info = home_map_info.get(map_flag)
        if map_info is None and map_flag is not None:
            map_info = home_map_info.get(str(map_flag))
        if map_info is None and home_map_info:
            map_info = next(iter(home_map_info.values()))
        sources.append(("home_map_info", attr(map_info, "rooms")))

    result: dict[int, dict[str, Any]] = {}
    # Priorität: explizite Rooms-API zuerst; weitere Quellen ergänzen fehlende Einträge.
    for source_name, source in sources:
        for entry in mapping_entries(source):
            segment_id = int_or_none(attr(entry, "segment_id", attr(entry, "id")))
            if segment_id is None:
                continue
            name = attr(entry, "raw_name", attr(entry, "iot_name"))
            iot_id = attr(entry, "iot_id", attr(entry, "iot_name_id"))
            candidate = {
                "name": str(name).strip() if name is not None else None,
                "iot_id": str(iot_id).strip() if iot_id not in (None, "") else None,
                "source_name": str(name).strip() if name is not None else None,
                "metadata_source": source_name,
            }
            old = result.get(segment_id)
            if old is None:
                result[segment_id] = candidate
                continue
            # Leere Home-Platzhalter dürfen gültige API-Namen nicht überschreiben.
            if not old.get("iot_id") and candidate.get("iot_id"):
                result[segment_id] = candidate
            elif not old.get("name") and candidate.get("name"):
                old["name"] = candidate["name"]
                old["source_name"] = candidate["source_name"]
                old["metadata_source"] = source_name
    return result


def room_geometry_to_dict(room: Any, room_number: int, metadata: dict[str, Any] | None) -> dict[str, Any]:
    bounds: dict[str, int | float | None] = {}
    for key in ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"):
        value = finite_number(attr(room, key))
        if value is not None:
            bounds[key] = value

    pos_x = finite_number(attr(room, "pos_x"))
    pos_y = finite_number(attr(room, "pos_y"))
    if pos_x is None or pos_y is None:
        # Für die bisher beobachteten Room-Objekte stehen x0/y0 und x1/y1
        # als rechteckige Grenzen zur Verfügung. Das ist ein Mittelpunkt-Näherungswert.
        x0, y0 = finite_number(bounds.get("x0")), finite_number(bounds.get("y0"))
        x1, y1 = finite_number(bounds.get("x1")), finite_number(bounds.get("y1"))
        if None not in (x0, y0, x1, y1):
            pos_x = round((x0 + x1) / 2)
            pos_y = round((y0 + y1) / 2)

    meta = metadata or {}
    raw_name = meta.get("name")
    iot_id = meta.get("iot_id")
    usable_name = bool(raw_name and raw_name.strip() and raw_name.strip().casefold() not in {"raum", "room", "unknown"})
    controllable = bool(usable_name and iot_id)
    display_name = raw_name if controllable else f"Unzugeordnet (Segment {room_number})"

    result = {
        "number": room_number,
        "segment_id": room_number,
        "name": display_name,
        "iot_id": iot_id if controllable else None,
        "controllable": controllable,
        "bounds": bounds,
        "position": {"x": pos_x, "y": pos_y},
    }
    if raw_name:
        result["source_name"] = raw_name
    if meta.get("metadata_source"):
        result["metadata_source"] = meta["metadata_source"]
    return result


def area_to_dict(area: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("x0", "y0", "x1", "y1", "x2", "y2", "x3", "y3"):
        value = finite_number(attr(area, key))
        if value is not None:
            result[key] = value
    if not result and isinstance(area, dict):
        return {str(k): to_plain(v) for k, v in area.items()}
    return result or to_plain(area)


def get_map_content(properties: Any, map_flag: int | None) -> tuple[Any, str]:
    """Sucht zuerst die MapContent aus HomeTrait, dann den direkten MapContent-Trait."""
    home = attr(properties, "home")
    content_map = attr(home, "home_map_content")
    if isinstance(content_map, dict) and content_map:
        content = content_map.get(map_flag)
        if content is None and map_flag is not None:
            content = content_map.get(str(map_flag))
        if content is None:
            content = next(iter(content_map.values()))
        return content, "home.home_map_content"

    direct = attr(properties, "map_content")
    if direct is not None and attr(direct, "image_content") is not None:
        return direct, "map_content"

    if isinstance(content_map, dict) and content_map:
        return next(iter(content_map.values())), "home.home_map_content (Fallback)"
    return direct, "map_content (Fallback)"


def normalize_png(image_content: Any) -> bytes:
    if isinstance(image_content, memoryview):
        image_content = image_content.tobytes()
    if isinstance(image_content, bytearray):
        image_content = bytes(image_content)
    if isinstance(image_content, bytes):
        png = image_content
    elif isinstance(image_content, str):
        text = image_content.strip()
        if text.startswith("data:image/png;base64,"):
            text = text.split(",", 1)[1]
        try:
            png = base64.b64decode(text, validate=True)
        except Exception as exc:
            raise ValueError("image_content liegt weder als PNG-Bytes noch als Base64-PNG vor.") from exc
    else:
        raise ValueError(f"Kein unterstützter Bildinhalt (Typ: {type(image_content).__name__}).")

    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Der gelieferte Bildinhalt hat keine gültige PNG-Signatur.")
    return png


def make_export(device: Any, properties: Any, map_content: Any, map_flag: int | None) -> dict[str, Any]:
    map_data = attr(map_content, "map_data")
    if map_data is None:
        raise RuntimeError("In den Kartendaten ist kein map_data-Objekt vorhanden.")

    metadata_by_segment = build_room_metadata(properties, map_flag)
    parser_rooms = attr(map_data, "rooms", {})
    rooms_out: list[dict[str, Any]] = []
    if isinstance(parser_rooms, dict):
        room_items = list(parser_rooms.items())
    else:
        room_items = [(attr(room, "number", index), room) for index, room in enumerate(mapping_entries(parser_rooms), start=1)]

    for key, room in room_items:
        segment_id = int_or_none(attr(room, "number", key))
        if segment_id is None:
            segment_id = int_or_none(key)
        if segment_id is None:
            continue
        rooms_out.append(room_geometry_to_dict(room, segment_id, metadata_by_segment.get(segment_id)))
    rooms_out.sort(key=lambda item: item["segment_id"])

    vacuum_position = point_to_dict(attr(map_data, "vacuum_position"))
    charger = point_to_dict(attr(map_data, "charger"))
    no_go_areas = [area_to_dict(area) for area in mapping_entries(attr(map_data, "no_go_areas", []))]
    no_mopping_areas = [area_to_dict(area) for area in mapping_entries(attr(map_data, "no_mopping_areas", []))]

    device_info = attr(device, "device_info")
    product = attr(device, "product")
    status = attr(properties, "status")
    maps = attr(properties, "maps")

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "device": attr(device, "name", DEFAULT_DEVICE_NAME),
        "model": attr(product, "model"),
        "firmware": attr(device_info, "fv"),
        "current_map": map_flag,
        "map_data": {
            "additional_parameters": to_plain(attr(map_data, "additional_parameters", {})),
            "calibration": calibration_to_json(map_data),
            "vacuum_position": vacuum_position,
            "vacuum_room": int_or_none(attr(map_data, "vacuum_room")),
            "vacuum_room_name": attr(map_data, "vacuum_room_name"),
            "charger": charger,
            "rooms": rooms_out,
            "no_go_areas": no_go_areas,
            "no_mopping_areas": no_mopping_areas,
        },
        "map_info": {
            "max_multi_map": attr(maps, "max_multi_map"),
            "max_bak_map": attr(maps, "max_bak_map"),
            "multi_map_count": attr(maps, "multi_map_count"),
            "map_name": attr(attr(maps, "current_map_info"), "name"),
            "robot_state": attr(attr(status, "state"), "value", attr(status, "state_name")),
        },
        "notes": [
            "controllable=true bedeutet, dass Name und IoT-ID aus Raum-Metadaten vorhanden sind.",
            "Unzugeordnete Segmente sind nur Kartengeometrie und dürfen nicht für Reinigungsbefehle verwendet werden.",
            "Raumpositionen sind vorhandene pos_x/pos_y oder ersatzweise der Mittelpunkt der rechteckigen Grenzen.",
        ],
    }


async def run_export(output_dir: Path, device_name: str) -> tuple[Path, Path]:
    device = None
    manager = None
    output_dir.mkdir(parents=True, exist_ok=True)

    email = input("Roborock-E-Mail-Adresse: ").strip()
    if not email:
        raise ValueError("Die E-Mail-Adresse darf nicht leer sein.")

    api = RoborockApiClient(username=email)
    log("Fordere Roborock-Anmeldecode an ...")
    await api.request_code()
    code = input("Roborock-Verifizierungscode: ").strip()
    if not code:
        raise ValueError("Der Verifizierungscode darf nicht leer sein.")
    user_data = await api.code_login(code)

    params = UserParams(
        username=email,
        user_data=user_data,
        base_url=await api.base_url,
    )

    try:
        log("Suche Roborock-Geräte ...")
        manager = await create_device_manager(params)
        devices = await manager.get_devices()
        device = next((item for item in devices if item.name == device_name), None)
        if device is None:
            names = ", ".join(str(attr(item, "name", "?")) for item in devices)
            raise RuntimeError(f"Gerät '{device_name}' nicht gefunden. Gefundene Geräte: {names or '(keine)'}")

        log(f"Gerät gefunden: {device.name}")
        properties = device.v1_properties
        for trait_name in ("status", "maps", "map_content", "rooms", "home"):
            await refresh_quietly(trait_name, attr(properties, trait_name))

        maps_trait = attr(properties, "maps")
        map_flag = int_or_none(attr(maps_trait, "current_map"))
        if map_flag is None:
            map_flag = int_or_none(attr(attr(properties, "status"), "current_map"))

        map_content, content_source = get_map_content(properties, map_flag)
        if map_content is None:
            raise RuntimeError("Die Roborock-API hat keine Kartendaten geliefert.")
        log(f"Verwende Kartenquelle: {content_source}")

        image_content = attr(map_content, "image_content")
        if image_content is None:
            # Nach home.refresh() kann home-MapContent noch leer sein; letzter Versuch direkt.
            direct_content = attr(properties, "map_content")
            image_content = attr(direct_content, "image_content")
            if image_content is not None and attr(map_content, "map_data") is None:
                map_content = direct_content
                log("Verwende Bild und Daten aus properties.map_content.")

        png_bytes = normalize_png(image_content)
        payload = make_export(device, properties, map_content, map_flag)

        image_path = output_dir / "roborock_map.png"
        json_path = output_dir / "roborock_map.json"
        image_tmp = image_path.with_suffix(".png.tmp")
        json_tmp = json_path.with_suffix(".json.tmp")

        image_tmp.write_bytes(png_bytes)
        json_tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        image_tmp.replace(image_path)
        json_tmp.replace(json_path)

        room_list = payload["map_data"]["rooms"]
        known = sum(1 for room in room_list if room["controllable"])
        unknown = len(room_list) - known
        log("")
        log(f"Kartenbild: {image_path}")
        log(f"JSON exportiert: {json_path}")
        log(f"Erfasste Raumsegmente: {len(room_list)}")
        log(f"Mit Name und IoT-ID: {known}")
        log(f"Unzugeordnet / nicht steuerbar: {unknown}")
        log("Export abgeschlossen.")
        return image_path, json_path
    finally:
        await close_quietly(device, "RoborockDevice")
        await close_quietly(manager, "DeviceManager")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roborock-Karte und Raumdaten exportieren")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Zielverzeichnis (Standard: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--device-name",
        default=DEFAULT_DEVICE_NAME,
        help=f"Gerätename (Standard: {DEFAULT_DEVICE_NAME})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run_export(args.output_dir, args.device_name))
        return 0
    except KeyboardInterrupt:
        log("Abbruch durch Benutzer.")
        return 130
    except Exception as exc:
        log(f"FEHLER: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
