from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .collector import CollectionError, SeasonVersion


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    season TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    version_start_time TEXT,
    fetched_at TEXT NOT NULL,
    hero_source_url TEXT NOT NULL,
    trait_source_url TEXT NOT NULL,
    hero_sha256 TEXT NOT NULL,
    trait_sha256 TEXT NOT NULL,
    equipment_source_url TEXT NOT NULL DEFAULT '',
    augment_source_url TEXT NOT NULL DEFAULT '',
    equipment_sha256 TEXT NOT NULL DEFAULT '',
    augment_sha256 TEXT NOT NULL DEFAULT '',
    UNIQUE(mode, season, version)
);

CREATE TABLE IF NOT EXISTS traits (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    trait_key TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('race', 'job')),
    name TEXT NOT NULL,
    picture TEXT,
    prefix TEXT,
    description TEXT,
    levels_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(season_id, trait_key, type)
);

CREATE TABLE IF NOT EXISTS heroes (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    hero_key TEXT NOT NULL,
    name TEXT NOT NULL,
    cost INTEGER NOT NULL,
    hero_paint TEXT,
    picture TEXT,
    picture_small TEXT,
    local_picture TEXT NOT NULL DEFAULT '',
    picture_big TEXT NOT NULL DEFAULT '',
    local_picture_big TEXT NOT NULL DEFAULT '',
    skill_name TEXT,
    skill_description TEXT,
    skill_icon TEXT,
    skill_values_json TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    origin_ids_json TEXT NOT NULL,
    map_ids_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(season_id, hero_key)
);

CREATE TABLE IF NOT EXISTS hero_traits (
    hero_id INTEGER NOT NULL REFERENCES heroes(id) ON DELETE CASCADE,
    trait_id INTEGER NOT NULL REFERENCES traits(id) ON DELETE CASCADE,
    PRIMARY KEY(hero_id, trait_id)
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    equipment_key TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    basic_description TEXT,
    description TEXT,
    picture TEXT,
    component_1_key TEXT,
    component_2_key TEXT,
    fetter_id TEXT,
    effect_type TEXT,
    sort INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    UNIQUE(season_id, equipment_key)
);

CREATE TABLE IF NOT EXISTS augments (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    augment_key TEXT NOT NULL,
    name TEXT NOT NULL,
    level INTEGER NOT NULL,
    description TEXT,
    icon TEXT,
    is_legend INTEGER NOT NULL DEFAULT 0,
    hero_enhancement_type TEXT,
    fetter_id TEXT,
    fetter_type TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(season_id, augment_key)
);

CREATE TABLE IF NOT EXISTS compositions (
    id INTEGER PRIMARY KEY,
    season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    board_size INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(season_id, name)
);

CREATE INDEX IF NOT EXISTS idx_heroes_season_cost ON heroes(season_id, cost);
CREATE INDEX IF NOT EXISTS idx_heroes_name ON heroes(name);
CREATE INDEX IF NOT EXISTS idx_traits_season_type ON traits(season_id, type);
CREATE INDEX IF NOT EXISTS idx_equipment_season_type ON equipment(season_id, type);
CREATE INDEX IF NOT EXISTS idx_augments_season_level ON augments(season_id, level);
CREATE INDEX IF NOT EXISTS idx_compositions_season ON compositions(season_id, updated_at DESC);
"""

MAX_BOARD_SIZE = 12
MAX_ITEMS_PER_HERO = 3
HEX_CELL_COUNT = 28
NAME_MAX_LENGTH = 40
NOTE_MAX_LENGTH = 200


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """打开事务连接，并保证提交、回滚后总会关闭底层连接。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def replace_season_data(
    db_path: Path,
    version: SeasonVersion,
    heroes: list[dict[str, Any]],
    traits: list[dict[str, Any]],
    equipment: list[dict[str, Any]],
    augments: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, int]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO seasons (
                mode, season, version, name, version_start_time, fetched_at,
                hero_source_url, trait_source_url, hero_sha256, trait_sha256,
                equipment_source_url, augment_source_url,
                equipment_sha256, augment_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mode, season, version) DO UPDATE SET
                name = excluded.name,
                version_start_time = excluded.version_start_time,
                fetched_at = excluded.fetched_at,
                hero_source_url = excluded.hero_source_url,
                trait_source_url = excluded.trait_source_url,
                hero_sha256 = excluded.hero_sha256,
                trait_sha256 = excluded.trait_sha256,
                equipment_source_url = excluded.equipment_source_url,
                augment_source_url = excluded.augment_source_url,
                equipment_sha256 = excluded.equipment_sha256,
                augment_sha256 = excluded.augment_sha256
            """,
            (
                version.mode,
                version.season,
                version.version,
                version.name,
                version.version_start_time,
                metadata["fetched_at"],
                metadata["hero_url"],
                metadata["trait_url"],
                metadata["hero_sha256"],
                metadata["trait_sha256"],
                metadata["equipment_url"],
                metadata["augment_url"],
                metadata["equipment_sha256"],
                metadata["augment_sha256"],
            ),
        )
        season_id = connection.execute(
            "SELECT id FROM seasons WHERE mode = ? AND season = ? AND version = ?",
            (version.mode, version.season, version.version),
        ).fetchone()["id"]
        _reject_suspicious_shrink(
            connection,
            season_id,
            {
                "heroes": len(heroes),
                "traits": len(traits),
                "equipment": len(equipment),
                "augments": len(augments),
            },
        )
        connection.execute("DELETE FROM hero_traits WHERE hero_id IN (SELECT id FROM heroes WHERE season_id = ?)", (season_id,))
        connection.execute("DELETE FROM heroes WHERE season_id = ?", (season_id,))
        connection.execute("DELETE FROM traits WHERE season_id = ?", (season_id,))
        connection.execute("DELETE FROM equipment WHERE season_id = ?", (season_id,))
        connection.execute("DELETE FROM augments WHERE season_id = ?", (season_id,))

        trait_ids: dict[tuple[str, str], int] = {}
        for trait in traits:
            cursor = connection.execute(
                """
                INSERT INTO traits (
                    season_id, trait_key, type, name, picture, prefix,
                    description, levels_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season_id,
                    trait["trait_key"],
                    trait["type"],
                    trait["name"],
                    trait["picture"],
                    trait["prefix"],
                    trait["description"],
                    _json(trait["levels"]),
                    _json(trait["raw"]),
                ),
            )
            trait_ids[(trait["type"], trait["trait_key"])] = cursor.lastrowid

        relation_count = 0
        for hero in heroes:
            cursor = connection.execute(
                """
                INSERT INTO heroes (
                    season_id, hero_key, name, cost, hero_paint, picture,
                    picture_small, local_picture, picture_big, local_picture_big,
                    skill_name, skill_description, skill_icon,
                    skill_values_json, stats_json, origin_ids_json, map_ids_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season_id,
                    hero["hero_key"],
                    hero["name"],
                    hero["cost"],
                    hero["hero_paint"],
                    hero["picture"],
                    hero["picture_small"],
                    hero.get("local_picture", ""),
                    hero.get("picture_big", ""),
                    hero.get("local_picture_big", ""),
                    hero["skill_name"],
                    hero["skill_description"],
                    hero["skill_icon"],
                    _json(hero["skill_values"]),
                    _json(hero["stats"]),
                    _json(hero["origin_ids"]),
                    _json(hero["map_ids"]),
                    _json(hero["raw"]),
                ),
            )
            hero_id = cursor.lastrowid
            for trait_type, keys in (("race", hero["race_ids"]), ("job", hero["job_ids"])):
                for key in keys:
                    trait_id = trait_ids.get((trait_type, key))
                    if trait_id is not None:
                        connection.execute(
                            "INSERT OR IGNORE INTO hero_traits(hero_id, trait_id) VALUES (?, ?)",
                            (hero_id, trait_id),
                        )
                        relation_count += 1

        for item in equipment:
            connection.execute(
                """
                INSERT INTO equipment (
                    season_id, equipment_key, name, type, basic_description,
                    description, picture, component_1_key, component_2_key,
                    fetter_id, effect_type, sort, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season_id,
                    item["equipment_key"],
                    item["name"],
                    item["type"],
                    item["basic_description"],
                    item["description"],
                    item["picture"],
                    item["component_1_key"],
                    item["component_2_key"],
                    item["fetter_id"],
                    item["effect_type"],
                    item["sort"],
                    _json(item["raw"]),
                ),
            )

        for item in augments:
            connection.execute(
                """
                INSERT INTO augments (
                    season_id, augment_key, name, level, description, icon,
                    is_legend, hero_enhancement_type, fetter_id, fetter_type, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season_id,
                    item["augment_key"],
                    item["name"],
                    item["level"],
                    item["description"],
                    item["icon"],
                    item["is_legend"],
                    item["hero_enhancement_type"],
                    item["fetter_id"],
                    item["fetter_type"],
                    _json(item["raw"]),
                ),
            )

    return {
        "heroes": len(heroes),
        "traits": len(traits),
        "hero_traits": relation_count,
        "equipment": len(equipment),
        "augments": len(augments),
    }


def _reject_suspicious_shrink(
    connection: sqlite3.Connection, season_id: int, incoming: dict[str, int]
) -> None:
    """同版本刷新若突然丢失三成以上实体，保留原数据并要求人工确认。"""
    for table, new_count in incoming.items():
        old_count = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE season_id = ?", (season_id,)
        ).fetchone()[0]
        if old_count >= 10 and new_count * 10 < old_count * 7:
            raise CollectionError(
                f"{table} 数据从 {old_count} 条降至 {new_count} 条，疑似采集不完整，已停止写库"
            )


def _migrate_seasons(connection: sqlite3.Connection) -> None:
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(seasons)").fetchall()
    }
    columns = {
        "equipment_source_url": "TEXT NOT NULL DEFAULT ''",
        "augment_source_url": "TEXT NOT NULL DEFAULT ''",
        "equipment_sha256": "TEXT NOT NULL DEFAULT ''",
        "augment_sha256": "TEXT NOT NULL DEFAULT ''",
    }
    for name, declaration in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE seasons ADD COLUMN {name} {declaration}")


def _migrate_heroes(connection: sqlite3.Connection) -> None:
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(heroes)").fetchall()
    }
    if "local_picture" not in existing:
        connection.execute(
            "ALTER TABLE heroes ADD COLUMN local_picture TEXT NOT NULL DEFAULT ''"
        )
    if "picture_big" not in existing:
        connection.execute(
            "ALTER TABLE heroes ADD COLUMN picture_big TEXT NOT NULL DEFAULT ''"
        )
    if "local_picture_big" not in existing:
        connection.execute(
            "ALTER TABLE heroes ADD COLUMN local_picture_big TEXT NOT NULL DEFAULT ''"
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ---------------- 阵容存档 ----------------


class CompositionError(Exception):
    """阵容存档操作失败的基类。"""


class InvalidComposition(CompositionError):
    """提交的阵容数据不合法。"""


class DuplicateName(CompositionError):
    """同一赛季下已有同名阵容。"""


class CompositionNotFound(CompositionError):
    """指定赛季下找不到该阵容。"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """对已有数据库补齐新表与新列，可重复执行。"""
    connection.executescript(SCHEMA)
    _migrate_seasons(connection)
    _migrate_heroes(connection)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_text(value: Any, field: str, limit: int, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidComposition(f"{field}必须是文本")
    text = value.strip()
    if required and not text:
        raise InvalidComposition(f"{field}不能为空")
    if len(text) > limit:
        raise InvalidComposition(f"{field}最长 {limit} 个字符")
    return text


def _valid_payload(
    connection: sqlite3.Connection,
    season_id: int,
    board_size: Any,
    payload: Any,
) -> tuple[int, dict[str, Any]]:
    """校验阵容内容，返回规范化后的 (人口, payload)。"""
    if not isinstance(board_size, int) or isinstance(board_size, bool):
        raise InvalidComposition("人口必须是整数")
    if not 1 <= board_size <= MAX_BOARD_SIZE:
        raise InvalidComposition(f"人口需在 1 到 {MAX_BOARD_SIZE} 之间")
    if not isinstance(payload, dict):
        raise InvalidComposition("阵容内容格式不正确")

    raw_board = payload.get("board", [])
    if not isinstance(raw_board, list):
        raise InvalidComposition("board 必须是英雄 id 列表")
    board: list[int] = []
    for value in raw_board:
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidComposition("board 只能包含英雄 id")
        if value in board:
            continue
        board.append(value)
    if len(board) > board_size:
        raise InvalidComposition("英雄数量超过人口上限")

    if board:
        marks = ",".join("?" * len(board))
        known = {
            row["id"]
            for row in connection.execute(
                f"SELECT id FROM heroes WHERE season_id = ? AND id IN ({marks})",
                (season_id, *board),
            )
        }
        missing = [hero_id for hero_id in board if hero_id not in known]
        if missing:
            raise InvalidComposition(f"英雄不属于当前赛季：{missing}")

    equipment_keys = {
        row["equipment_key"]
        for row in connection.execute(
            "SELECT equipment_key FROM equipment WHERE season_id = ?", (season_id,)
        )
    }
    raw_items = payload.get("items", {})
    if not isinstance(raw_items, dict):
        raise InvalidComposition("items 必须是英雄到装备的映射")
    items: dict[str, list[str]] = {}
    for raw_hero, raw_keys in raw_items.items():
        hero_id = _as_hero_id(raw_hero)
        if hero_id not in board:
            raise InvalidComposition("items 里出现了不在阵容中的英雄")
        if not isinstance(raw_keys, list):
            raise InvalidComposition("每位英雄的装备必须是列表")
        if len(raw_keys) > MAX_ITEMS_PER_HERO:
            raise InvalidComposition(f"每位英雄最多 {MAX_ITEMS_PER_HERO} 件装备")
        keys = []
        for key in raw_keys:
            if not isinstance(key, str) or key not in equipment_keys:
                raise InvalidComposition(f"装备不属于当前赛季：{key!r}")
            keys.append(key)
        if keys:
            items[str(hero_id)] = keys

    raw_positions = payload.get("positions", {})
    if not isinstance(raw_positions, dict):
        raise InvalidComposition("positions 必须是格子到英雄的映射")
    positions: dict[str, int] = {}
    for raw_cell, raw_hero in raw_positions.items():
        try:
            cell = int(raw_cell)
        except (TypeError, ValueError) as exc:
            raise InvalidComposition("站位格序号必须是整数") from exc
        if not 0 <= cell < HEX_CELL_COUNT:
            raise InvalidComposition("站位格序号超出棋盘范围")
        hero_id = _as_hero_id(raw_hero)
        if hero_id not in board:
            raise InvalidComposition("positions 里出现了不在阵容中的英雄")
        positions[str(cell)] = hero_id
    if len(set(positions.values())) != len(positions):
        raise InvalidComposition("同一个英雄不能占据多个格子")

    return board_size, {"board": board, "items": items, "positions": positions}


def _as_hero_id(value: Any) -> int:
    if isinstance(value, bool):
        raise InvalidComposition("英雄 id 不合法")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise InvalidComposition(f"英雄 id 不合法：{value!r}") from exc
    raise InvalidComposition("英雄 id 不合法")


def _row_to_composition(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        "id": row["id"],
        "season_id": row["season_id"],
        "name": row["name"],
        "note": row["note"],
        "board_size": row["board_size"],
        "board": payload.get("board", []),
        "items": payload.get("items", {}),
        "positions": payload.get("positions", {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_compositions(db_path: Path, season_id: int) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT id, season_id, name, note, board_size, payload_json, created_at, updated_at
            FROM compositions WHERE season_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (season_id,),
        ).fetchall()
    return [_row_to_composition(row) for row in rows]


def create_composition(
    db_path: Path,
    season_id: int,
    name: str,
    note: str,
    board_size: Any,
    payload: Any,
) -> dict[str, Any]:
    with connect(db_path) as connection:
        ensure_schema(connection)
        _require_season(connection, season_id)
        clean_name = _clean_text(name, "阵容名称", NAME_MAX_LENGTH, required=True)
        clean_note = _clean_text(note, "备注", NOTE_MAX_LENGTH, required=False)
        size, body = _valid_payload(connection, season_id, board_size, payload)
        stamp = _now()
        try:
            cursor = connection.execute(
                """
                INSERT INTO compositions (
                    season_id, name, note, board_size, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (season_id, clean_name, clean_note, size, _json(body), stamp, stamp),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateName(f"已存在同名阵容：{clean_name}") from exc
        row = connection.execute(
            """
            SELECT id, season_id, name, note, board_size, payload_json, created_at, updated_at
            FROM compositions WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return _row_to_composition(row)


def update_composition(
    db_path: Path,
    season_id: int,
    composition_id: int,
    name: str | None = None,
    note: str | None = None,
    board_size: Any = None,
    payload: Any = None,
) -> dict[str, Any]:
    """只更新传入的字段；阵容内容与人口必须同时给出。"""
    with connect(db_path) as connection:
        ensure_schema(connection)
        current = connection.execute(
            """
            SELECT id, season_id, name, note, board_size, payload_json, created_at, updated_at
            FROM compositions WHERE id = ? AND season_id = ?
            """,
            (composition_id, season_id),
        ).fetchone()
        if current is None:
            raise CompositionNotFound(f"找不到阵容 {composition_id}")

        clean_name = current["name"] if name is None else _clean_text(
            name, "阵容名称", NAME_MAX_LENGTH, required=True
        )
        clean_note = current["note"] if note is None else _clean_text(
            note, "备注", NOTE_MAX_LENGTH, required=False
        )
        if payload is None and board_size is None:
            size, body_json = current["board_size"], current["payload_json"]
        elif payload is None or board_size is None:
            raise InvalidComposition("修改阵容内容时必须同时提供人口与阵容")
        else:
            size, body = _valid_payload(connection, season_id, board_size, payload)
            body_json = _json(body)

        try:
            connection.execute(
                """
                UPDATE compositions
                SET name = ?, note = ?, board_size = ?, payload_json = ?, updated_at = ?
                WHERE id = ? AND season_id = ?
                """,
                (clean_name, clean_note, size, body_json, _now(), composition_id, season_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateName(f"已存在同名阵容：{clean_name}") from exc
        row = connection.execute(
            """
            SELECT id, season_id, name, note, board_size, payload_json, created_at, updated_at
            FROM compositions WHERE id = ?
            """,
            (composition_id,),
        ).fetchone()
    return _row_to_composition(row)


def delete_composition(db_path: Path, season_id: int, composition_id: int) -> None:
    with connect(db_path) as connection:
        ensure_schema(connection)
        cursor = connection.execute(
            "DELETE FROM compositions WHERE id = ? AND season_id = ?",
            (composition_id, season_id),
        )
        if cursor.rowcount == 0:
            raise CompositionNotFound(f"找不到阵容 {composition_id}")


def _require_season(connection: sqlite3.Connection, season_id: int) -> None:
    row = connection.execute("SELECT id FROM seasons WHERE id = ?", (season_id,)).fetchone()
    if row is None:
        raise InvalidComposition(f"赛季不存在：{season_id}")
