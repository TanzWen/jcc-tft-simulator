from __future__ import annotations

import hashlib
from http.client import IncompleteRead
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


OFFICIAL_CONFIG_URL = "https://jcc.qq.com/data-js/basicConfig.js"
VERSION_INDEX_URL = (
    "https://game.gtimg.cn/images/lol/act/jkzlk/js/config/versiondataconfig.js"
)
GAME_DATA_BASE_URL = "https://game.gtimg.cn/images/lol/act/jkzlk/js/"
USER_AGENT = "jcc-data/0.1 (+https://jcc.qq.com/)"
MIN_COLLECTION_COUNTS = {
    "heroes": 20,
    "traits": 8,
    "equipment": 20,
    "augments": 20,
}


class CollectionError(RuntimeError):
    """官网数据无法获取或格式不符合预期。"""


@dataclass(frozen=True)
class SeasonVersion:
    mode: str
    season: str
    version: str
    name: str
    version_start_time: str
    hero_path: str
    trait_path: str
    equipment_path: str = ""
    augment_path: str = ""

    @property
    def key(self) -> str:
        return f"mode{self.mode}_{self.season}_{self.version}"


def fetch_bytes(url: str, *, timeout: float = 30, retries: int = 3) -> bytes:
    """以较低频率读取固定的官网公开资源。"""
    last_error: Exception | None = None
    for attempt in range(retries):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/javascript,*/*;q=0.8",
                "Referer": "https://jcc.qq.com/",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, IncompleteRead, OSError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
    raise CollectionError(f"请求失败：{url}（{last_error}）") from last_error


def fetch_text(url: str) -> str:
    payload = fetch_bytes(url)
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise CollectionError(f"无法识别官网文本编码：{url}")


def fetch_json(url: str) -> Any:
    text = fetch_text(url)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        preview = re.sub(r"\s+", " ", text[:120])
        raise CollectionError(f"官网返回的不是 JSON：{url}（{preview!r}）") from exc


def parse_current_season(config_js: str) -> tuple[str, str]:
    mode_match = re.search(r"\bvar\s+mode\s*=\s*['\"]([^'\"]+)", config_js)
    season_match = re.search(r"\bvar\s+season\s*=\s*['\"]([^'\"]+)", config_js)
    if not mode_match or not season_match:
        raise CollectionError("无法从官网 basicConfig.js 识别当前模式和赛季")
    return mode_match.group(1), normalize_season(season_match.group(1))


def parse_picture_template(
    config_js: str, mode: str, season: str, group: str = "hero_pic_big"
) -> str:
    """从官网 basicConfig.js 读取指定模式的图片 URL 模板。"""
    block = re.search(
        rf"\b{re.escape(group)}\s*:\s*\{{(?P<body>.*?)\n\s*\}},",
        config_js,
        re.DOTALL,
    )
    if not block:
        raise CollectionError(f"官网配置中缺少 {group}")
    key = f"{mode}_{normalize_season(season)}"
    entry = re.search(
        rf"['\"]{re.escape(key)}['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        block.group("body"),
    )
    if not entry:
        raise CollectionError(f"官网 {group} 中缺少 {key} 图片模板")
    template = entry.group(1)
    return f"https:{template}" if template.startswith("//") else template


def normalize_season(value: str) -> str:
    value = str(value).strip()
    return f"S{value.lstrip('sS')}"


def select_version(
    records: list[dict[str, Any]],
    mode: str,
    season: str,
    requested_version: str | None = None,
    today: date | None = None,
) -> SeasonVersion:
    season = normalize_season(season)
    matches = [
        item
        for item in records
        if str(item.get("mode")) == str(mode)
        and normalize_season(str(item.get("season", ""))) == season
    ]
    if requested_version:
        matches = [item for item in matches if item.get("version") == requested_version]
    if not matches:
        suffix = f"，版本 {requested_version}" if requested_version else ""
        raise CollectionError(f"官网版本索引中没有 mode={mode}、season={season}{suffix}")

    today = today or date.today()

    def start_date(item: dict[str, Any]) -> date:
        raw = str(item.get("version_start_time") or "")
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return date.min

    eligible = [item for item in matches if start_date(item) <= today]
    if requested_version is None:
        if not eligible:
            raise CollectionError(
                f"mode={mode}、season={season} 没有截至 {today.isoformat()} 已生效的版本"
            )
        matches = eligible

    # 官网可能提前发布下一版本；开始日期优先，is_newest_version 用于同日消歧。
    selected = max(
        matches,
        key=lambda item: (
            start_date(item),
            int(item.get("is_newest_version") or 0),
            _natural_version_key(str(item.get("version", ""))),
        ),
    )
    return SeasonVersion(
        mode=str(selected["mode"]),
        season=normalize_season(str(selected["season"])),
        version=str(selected["version"]),
        name=str(selected.get("name") or ""),
        version_start_time=str(selected.get("version_start_time") or ""),
        hero_path=str(selected["herourl"]),
        trait_path=str(selected["traiturl"]),
        equipment_path=str(selected["equipurl"]),
        augment_path=str(selected["hexurl"]),
    )


def _natural_version_key(value: str) -> tuple[tuple[int, Any], ...]:
    parts = re.findall(r"\d+|\D+", value)
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


def transform_traits(payload: dict[str, Any], version: SeasonVersion) -> list[dict[str, Any]]:
    if str(payload.get("setId")) != version.mode or not isinstance(payload.get("data"), dict):
        raise CollectionError("羁绊数据的 setId 或 data 字段异常")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in payload["data"].values():
        if str(item.get("setid")) != version.mode:
            continue
        if not all(item.get(key) not in (None, "") for key in ("checkId", "color", "num", "level")):
            continue
        trait_type = "race" if int(item.get("type", -1)) == 0 else "job"
        groups[(trait_type, str(item["checkId"]))].append(item)

    traits: list[dict[str, Any]] = []
    for (trait_type, trait_key), levels in groups.items():
        levels.sort(key=lambda item: int(item.get("level") or 0))
        first = levels[0]
        traits.append(
            {
                "trait_key": trait_key,
                "type": trait_type,
                "name": first.get("name", ""),
                "picture": first.get("picture", ""),
                "prefix": first.get("prefix", ""),
                "description": first.get("desc2", ""),
                "levels": [
                    {
                        "level": int(item.get("level") or 0),
                        "count": int(item.get("num") or 0),
                        "color": int(item.get("color") or 0),
                        "effect": item.get("realDesc", ""),
                    }
                    for item in levels
                ],
                "raw": levels,
            }
        )
    return sorted(traits, key=lambda item: (item["type"], item["name"], item["trait_key"]))


def transform_heroes(payload: dict[str, Any], version: SeasonVersion) -> list[dict[str, Any]]:
    if str(payload.get("setId")) != version.mode or not isinstance(payload.get("data"), dict):
        raise CollectionError("英雄数据的 setId 或 data 字段异常")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for origin_id, source in payload["data"].items():
        if str(source.get("setid")) != version.mode:
            continue
        item = dict(source)
        if item.get("$same_id"):
            hero_key = str(item["$same_id"])
            star = int(item.get("$star") or 1)
        else:
            origin_id = str(origin_id)
            hero_key = origin_id[1:]
            star = int(origin_id[0]) if origin_id[:1].isdigit() else 1
        item["_origin_id"] = str(origin_id)
        item["_star"] = star
        groups[hero_key].append(item)

    heroes: list[dict[str, Any]] = []
    stat_fields = (
        "armor",
        "attackRange",
        "attackSpeed",
        "criticalStrikeChance",
        "initAttackDamage",
        "initHP",
        "initMP",
        "magicResist",
        "maxMP",
    )
    for hero_key, stars in groups.items():
        stars.sort(key=lambda item: item["_star"])
        base = stars[0]
        if int(base.get("heroType", -1)) != 0 or str(base.get("showHeroTag")) != "1":
            continue
        stats = {
            field: [item.get(field) for item in stars]
            for field in stat_fields
        }
        heroes.append(
            {
                "hero_key": hero_key,
                "name": base.get("name", ""),
                "cost": int(base.get("sellPrice") or base.get("price") or 0),
                "hero_paint": base.get("heroPaint", ""),
                "picture": base.get("picture", ""),
                "picture_small": str(base.get("picture") or "")
                .replace(".png", ".jpg")
                .replace("/hero/", "/hero72/"),
                "skill_name": base.get("skillName", ""),
                "skill_description": base.get("skillDesc", ""),
                "skill_icon": base.get("skillIcon", ""),
                "skill_values": str(base.get("skillValueDesc") or "").split("|")
                if base.get("skillValueDesc")
                else [],
                "race_ids": _split_ids(base.get("species")),
                "job_ids": _split_ids(base.get("class")),
                "stats": stats,
                "origin_ids": [item["_origin_id"] for item in stars],
                "map_ids": [item.get("mapID") for item in stars],
                "raw": stars,
            }
        )
    return sorted(heroes, key=lambda item: (item["cost"], item["name"], item["hero_key"]))


def transform_equipment(
    payload: dict[str, Any], version: SeasonVersion
) -> list[dict[str, Any]]:
    if str(payload.get("setId")) != version.mode or not isinstance(payload.get("data"), dict):
        raise CollectionError("装备数据的 setId 或 data 字段异常")

    equipment: list[dict[str, Any]] = []
    for source_key, source in payload["data"].items():
        if str(source.get("planID")) != version.mode or str(source.get("type")) == "-1":
            continue
        equipment.append(
            {
                "equipment_key": str(source.get("id") or source_key),
                "name": source.get("name", ""),
                "type": source.get("type", ""),
                "basic_description": source.get("basicDesc", ""),
                "description": source.get("desc", ""),
                "picture": source.get("picture", ""),
                "component_1_key": _optional_id(source.get("synthesis1")),
                "component_2_key": _optional_id(source.get("synthesis2")),
                "fetter_id": _optional_id(source.get("fetterID")),
                "effect_type": source.get("EffectType", ""),
                "sort": int(source.get("sort") or 0),
                "raw": source,
            }
        )
    return sorted(
        equipment,
        key=lambda item: (item["sort"], item["type"], item["name"], item["equipment_key"]),
    )


def transform_augments(
    payload: dict[str, Any], version: SeasonVersion
) -> list[dict[str, Any]]:
    if str(payload.get("setId")) != version.mode or not isinstance(payload.get("data"), dict):
        raise CollectionError("强化符文数据的 setId 或 data 字段异常")

    augments: list[dict[str, Any]] = []
    for source_key, source in payload["data"].items():
        if not source.get("id") or not source.get("level"):
            continue
        augments.append(
            {
                "augment_key": str(source.get("id") or source_key),
                "name": source.get("name", ""),
                "level": int(source.get("level") or 0),
                "description": source.get("desc", ""),
                "icon": source.get("icon", ""),
                "is_legend": int(source.get("is_legend") or 0),
                "hero_enhancement_type": str(source.get("hero_enhancement_type") or ""),
                "fetter_id": _optional_id(source.get("fetterId")),
                "fetter_type": str(source.get("fetterType") or ""),
                "raw": source,
            }
        )
    return sorted(augments, key=lambda item: (item["level"], item["name"], item["augment_key"]))


def _split_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    return [part for part in str(value).split("|") if part]


def _optional_id(value: Any) -> str | None:
    if value in (None, "", "0", 0):
        return None
    return str(value)


def collect(
    raw_root: Path,
    *,
    mode: str | None = None,
    season: str | None = None,
    requested_version: str | None = None,
) -> tuple[
    SeasonVersion,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    config_text = fetch_text(OFFICIAL_CONFIG_URL)
    current_mode, current_season = parse_current_season(config_text)
    mode = str(mode or current_mode)
    season = normalize_season(season or current_season)

    version_records = fetch_json(VERSION_INDEX_URL)
    if not isinstance(version_records, list):
        raise CollectionError("官网版本索引不是数组")
    selected = select_version(version_records, mode, season, requested_version)

    hero_url = urljoin(GAME_DATA_BASE_URL, selected.hero_path.lstrip("/"))
    trait_url = urljoin(GAME_DATA_BASE_URL, selected.trait_path.lstrip("/"))
    equipment_url = urljoin(GAME_DATA_BASE_URL, selected.equipment_path.lstrip("/"))
    augment_url = urljoin(GAME_DATA_BASE_URL, selected.augment_path.lstrip("/"))
    hero_payload = fetch_json(hero_url)
    trait_payload = fetch_json(trait_url)
    equipment_payload = fetch_json(equipment_url)
    augment_payload = fetch_json(augment_url)
    heroes = transform_heroes(hero_payload, selected)
    picture_big_template = parse_picture_template(
        config_text, selected.mode, selected.season, "hero_pic_big"
    )
    for hero in heroes:
        hero["picture_big"] = picture_big_template.replace(
            "{{pic_name}}", str(hero["hero_paint"])
        )
    traits = transform_traits(trait_payload, selected)
    equipment = transform_equipment(equipment_payload, selected)
    augments = transform_augments(augment_payload, selected)
    counts = {
        "heroes": len(heroes),
        "traits": len(traits),
        "equipment": len(equipment),
        "augments": len(augments),
    }
    too_small = [
        f"{name}={count}（至少 {MIN_COLLECTION_COUNTS[name]}）"
        for name, count in counts.items()
        if count < MIN_COLLECTION_COUNTS[name]
    ]
    if too_small:
        raise CollectionError(f"官网数据解析数量异常：{'；'.join(too_small)}，已停止写库")

    snapshot_dir = raw_root / selected.key
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "basicConfig.js": config_text,
        "version_index.json": version_records,
        "chess.json": hero_payload,
        "trait.json": trait_payload,
        "equip.json": equipment_payload,
        "hex.json": augment_payload,
    }
    for filename, content in files.items():
        path = snapshot_dir / filename
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    metadata = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "current_official_mode": current_mode,
        "current_official_season": current_season,
        "hero_url": hero_url,
        "trait_url": trait_url,
        "equipment_url": equipment_url,
        "augment_url": augment_url,
        "version_index_url": VERSION_INDEX_URL,
        "picture_big_template": picture_big_template,
        "hero_sha256": hashlib.sha256(
            json.dumps(hero_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "trait_sha256": hashlib.sha256(
            json.dumps(trait_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "equipment_sha256": hashlib.sha256(
            json.dumps(equipment_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "augment_sha256": hashlib.sha256(
            json.dumps(augment_payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "snapshot_dir": str(snapshot_dir),
    }
    (snapshot_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return selected, heroes, traits, equipment, augments, metadata
