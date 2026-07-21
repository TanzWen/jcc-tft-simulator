from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from ..database import (
    CompositionError,
    CompositionNotFound,
    DuplicateName,
    InvalidComposition,
    connect,
    create_composition,
    delete_composition,
    ensure_schema,
    list_compositions,
    update_composition,
)


STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 256 * 1024


class SeasonNotFound(Exception):
    """数据库中没有可用赛季数据。"""


def load_season(db_path: Path, season_id: int | None = None) -> dict[str, Any]:
    """读取一个赛季的英雄、羁绊与关联关系，返回可直接序列化的字典。"""
    if not db_path.exists():
        raise SeasonNotFound(f"找不到数据库 {db_path}，请先运行 jcc 采集数据")

    with connect(db_path) as connection:
        ensure_schema(connection)
        try:
            seasons = [dict(row) for row in connection.execute(
                """
                SELECT id, mode, season, version, name, version_start_time, fetched_at
                FROM seasons ORDER BY version_start_time DESC, id DESC
                """
            )]
        except sqlite3.OperationalError as exc:
            raise SeasonNotFound(f"数据库结构不完整：{exc}") from exc

        if not seasons:
            raise SeasonNotFound("数据库中没有赛季数据，请先运行 jcc 采集数据")

        current = next((s for s in seasons if s["id"] == season_id), seasons[0])
        sid = current["id"]

        traits = []
        for row in connection.execute(
            """
            SELECT id, trait_key, type, name, picture, prefix, description, levels_json
            FROM traits WHERE season_id = ? ORDER BY type, name
            """,
            (sid,),
        ):
            trait = dict(row)
            trait["levels"] = json.loads(trait.pop("levels_json"))
            traits.append(trait)

        heroes = []
        for row in connection.execute(
            """
            SELECT id, hero_key, name, cost, picture, picture_small, local_picture,
                   picture_big, local_picture_big,
                   skill_name, skill_description, skill_icon, skill_values_json, stats_json
            FROM heroes WHERE season_id = ? ORDER BY cost, name
            """,
            (sid,),
        ):
            hero = dict(row)
            hero["skill_values"] = json.loads(hero.pop("skill_values_json"))
            hero["stats"] = json.loads(hero.pop("stats_json"))
            hero["picture_local"] = (
                "/assets/" + quote(hero["local_picture"], safe="/")
                if hero["local_picture"]
                else ""
            )
            hero["picture_big_local"] = (
                "/assets/" + quote(hero["local_picture_big"], safe="/")
                if hero["local_picture_big"]
                else ""
            )
            hero["trait_ids"] = []
            heroes.append(hero)

        by_id = {hero["id"]: hero for hero in heroes}
        for row in connection.execute(
            """
            SELECT ht.hero_id, ht.trait_id FROM hero_traits ht
            JOIN heroes h ON h.id = ht.hero_id WHERE h.season_id = ?
            """,
            (sid,),
        ):
            hero = by_id.get(row["hero_id"])
            if hero is not None:
                hero["trait_ids"].append(row["trait_id"])

        # 转职纹章的 fetter_id 对应 traits.trait_key，换算成前端直接可用的 trait_id
        trait_id_by_key = {trait["trait_key"]: trait["id"] for trait in traits}
        equipment = []
        for row in connection.execute(
            """
            SELECT id, equipment_key, name, type, basic_description, description,
                   picture, component_1_key, component_2_key, fetter_id, sort
            FROM equipment WHERE season_id = ? ORDER BY type, sort, name
            """,
            (sid,),
        ):
            item = dict(row)
            item["trait_id"] = trait_id_by_key.get(item["fetter_id"] or "")
            equipment.append(item)

    return {
        "season": current,
        "seasons": seasons,
        "traits": traits,
        "heroes": heroes,
        "equipment": equipment,
    }


def current_season_id(db_path: Path) -> int:
    """默认赛季与 /api/season 保持一致：按生效时间取最新的一个。"""
    if not db_path.exists():
        raise SeasonNotFound(f"找不到数据库 {db_path}，请先运行 jcc 采集数据")
    with connect(db_path) as connection:
        ensure_schema(connection)
        try:
            row = connection.execute(
                """
                SELECT id FROM seasons ORDER BY version_start_time DESC, id DESC LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise SeasonNotFound(f"数据库结构不完整：{exc}") from exc
    if row is None:
        raise SeasonNotFound("数据库中没有赛季数据，请先运行 jcc 采集数据")
    return int(row["id"])


class Handler(BaseHTTPRequestHandler):
    server_version = "jcc-web"

    def __init__(self, *args: Any, db_path: Path, **kwargs: Any) -> None:
        self.db_path = db_path
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定
        path = self.path.split("?", 1)[0]
        if path == "/api/season":
            self._serve_season()
        elif path == "/api/compositions":
            self._list_compositions()
        elif path.startswith("/assets/"):
            self._serve_asset(path)
        else:
            self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        if self.path.split("?", 1)[0] != "/api/compositions":
            self.send_error(404, "Not Found")
            return
        self._create_composition()

    def do_PUT(self) -> None:  # noqa: N802 - http.server 约定
        composition_id = self._composition_id()
        if composition_id is None:
            return
        self._update_composition(composition_id)

    def do_DELETE(self) -> None:  # noqa: N802 - http.server 约定
        composition_id = self._composition_id()
        if composition_id is None:
            return
        self._delete_composition(composition_id)

    # ---------------- 阵容存档 ----------------

    def _composition_id(self) -> int | None:
        """解析 /api/compositions/{id}；不匹配时直接回 404 并返回 None。"""
        path = self.path.split("?", 1)[0]
        prefix = "/api/compositions/"
        if path.startswith(prefix):
            tail = path[len(prefix):]
            if tail.isdigit():
                return int(tail)
        self.send_error(404, "Not Found")
        return None

    def _season_id(self) -> int | None:
        """优先用查询参数指定的赛季，否则回退到当前赛季。"""
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        raw = (query.get("season_id") or [""])[0]
        try:
            if raw:
                if not raw.isdigit():
                    raise InvalidComposition(f"season_id 不合法：{raw!r}")
                return int(raw)
            return current_season_id(self.db_path)
        except SeasonNotFound as exc:
            self._send_json({"error": str(exc)}, status=503)
        except InvalidComposition as exc:
            self._send_json({"error": str(exc)}, status=400)
        return None

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json({"error": "Content-Length 不合法"}, status=400)
            return None
        if length <= 0:
            self._send_json({"error": "请求体为空"}, status=400)
            return None
        if length > MAX_BODY_BYTES:
            self._send_json({"error": "请求体过大"}, status=413)
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json({"error": f"请求体不是合法 JSON：{exc}"}, status=400)
            return None
        if not isinstance(body, dict):
            self._send_json({"error": "请求体必须是 JSON 对象"}, status=400)
            return None
        return body

    def _send_composition_error(self, exc: CompositionError) -> None:
        status = 400
        if isinstance(exc, CompositionNotFound):
            status = 404
        elif isinstance(exc, DuplicateName):
            status = 409
        self._send_json({"error": str(exc)}, status=status)

    def _list_compositions(self) -> None:
        season_id = self._season_id()
        if season_id is None:
            return
        items = list_compositions(self.db_path, season_id)
        self._send_json({"season_id": season_id, "compositions": items})

    def _create_composition(self) -> None:
        season_id = self._season_id()
        if season_id is None:
            return
        body = self._read_json()
        if body is None:
            return
        try:
            item = create_composition(
                self.db_path,
                season_id,
                body.get("name", ""),
                body.get("note", ""),
                body.get("board_size"),
                {
                    "board": body.get("board", []),
                    "items": body.get("items", {}),
                    "positions": body.get("positions", {}),
                },
            )
        except CompositionError as exc:
            self._send_composition_error(exc)
            return
        self._send_json({"composition": item}, status=201)

    def _update_composition(self, composition_id: int) -> None:
        season_id = self._season_id()
        if season_id is None:
            return
        body = self._read_json()
        if body is None:
            return
        # 只改名或只改备注时不带 board_size，阵容内容保持原样
        has_board = "board_size" in body
        try:
            item = update_composition(
                self.db_path,
                season_id,
                composition_id,
                name=body.get("name"),
                note=body.get("note"),
                board_size=body.get("board_size") if has_board else None,
                payload={
                    "board": body.get("board", []),
                    "items": body.get("items", {}),
                    "positions": body.get("positions", {}),
                } if has_board else None,
            )
        except CompositionError as exc:
            self._send_composition_error(exc)
            return
        self._send_json({"composition": item})

    def _delete_composition(self, composition_id: int) -> None:
        season_id = self._season_id()
        if season_id is None:
            return
        try:
            delete_composition(self.db_path, season_id, composition_id)
        except CompositionError as exc:
            self._send_composition_error(exc)
            return
        self._send_json({"deleted": composition_id})

    def _serve_season(self) -> None:
        try:
            payload = load_season(self.db_path)
        except SeasonNotFound as exc:
            self._send_json({"error": str(exc)}, status=503)
            return
        self._send_json(payload)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self.send_error(404, "Not Found")
            return
        content = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _serve_asset(self, path: str) -> None:
        assets_dir = (self.db_path.parent / "assets").resolve()
        target = (assets_dir / path.removeprefix("/assets/")).resolve()
        if not target.is_file() or assets_dir not in target.parents:
            self.send_error(404, "Not Found")
            return
        content = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动金铲铲阵容模拟器前端")
    parser.add_argument("--db", type=Path, default=Path("data/jcc.db"), help="SQLite 文件路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8787, help="监听端口")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = partial(Handler, db_path=args.db.resolve())
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"阵容模拟器已启动：{url}（Ctrl+C 退出）")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
