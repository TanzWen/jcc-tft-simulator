from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .collector import CollectionError, SeasonVersion, fetch_bytes


def attach_cached_hero_images(
    heroes: list[dict[str, Any]], version: SeasonVersion, assets_root: Path
) -> dict[str, int]:
    """仅关联已经存在的本地图片，不发起网络请求。"""
    cached = 0
    total = 0
    for hero in heroes:
        for local_field, _, relative in _image_variants(hero, version):
            total += 1
            destination = assets_root / relative
            if destination.is_file() and _valid_image(destination.read_bytes()):
                hero[local_field] = relative.as_posix()
                cached += 1
            else:
                hero[local_field] = ""
    return {"downloaded": 0, "cached": cached, "total": total}


def download_hero_images(
    heroes: list[dict[str, Any]],
    version: SeasonVersion,
    assets_root: Path,
    *,
    workers: int = 4,
) -> dict[str, int]:
    """下载英雄原图并将相对路径写入英雄数据。"""
    (assets_root / "heroes" / version.key).mkdir(parents=True, exist_ok=True)
    cached = 0
    pending: list[tuple[dict[str, Any], str, str, Path, str]] = []
    total = 0

    for hero in heroes:
        variants = _image_variants(hero, version)
        if not variants:
            raise CollectionError(f"英雄 {hero.get('name')} 缺少图片地址")
        for local_field, url, relative in variants:
            total += 1
            destination = assets_root / relative
            hero[local_field] = relative.as_posix()
            if destination.is_file() and _valid_image(destination.read_bytes()):
                cached += 1
            else:
                pending.append(
                    (hero, local_field, url, destination, relative.as_posix())
                )

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
        futures = {
            executor.submit(_download_one, url, destination): (hero, local_field, relative)
            for hero, local_field, url, destination, relative in pending
        }
        for future in as_completed(futures):
            hero, local_field, relative = futures[future]
            try:
                future.result()
                hero[local_field] = relative
            except Exception as exc:  # 汇总所有失败项，避免只报告第一张图片。
                failures.append(f"{hero.get('name')}：{exc}")

    if failures:
        preview = "；".join(failures[:5])
        raise CollectionError(f"有 {len(failures)} 张英雄图片下载失败：{preview}")
    return {"downloaded": len(pending), "cached": cached, "total": total}


def _download_one(url: str, destination: Path) -> None:
    payload = fetch_bytes(url)
    if not _valid_image(payload):
        raise CollectionError("官网响应不是有效图片")
    temporary = destination.with_suffix(destination.suffix + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(payload)
    temporary.replace(destination)


def _image_variants(
    hero: dict[str, Any], version: SeasonVersion
) -> list[tuple[str, str, Path]]:
    variants: list[tuple[str, str, Path]] = []
    avatar_url = str(hero.get("picture") or "")
    if avatar_url:
        variants.append(
            (
                "local_picture",
                avatar_url,
                Path("heroes")
                / version.key
                / f"{hero['hero_key']}{_image_extension(avatar_url)}",
            )
        )
    big_url = str(hero.get("picture_big") or "")
    if big_url:
        variants.append(
            (
                "local_picture_big",
                big_url,
                Path("heroes")
                / version.key
                / "full"
                / f"{hero['hero_key']}{_image_extension(big_url)}",
            )
        )
    return variants


def _image_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".img"


def _valid_image(payload: bytes) -> bool:
    return (
        payload.startswith(b"\x89PNG\r\n\x1a\n")
        or payload.startswith(b"\xff\xd8\xff")
        or payload.startswith((b"GIF87a", b"GIF89a"))
        or (len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP")
    )
