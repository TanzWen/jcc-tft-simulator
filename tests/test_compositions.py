import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jcc.database import (
    SCHEMA,
    _reject_suspicious_shrink,
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
from jcc.collector import CollectionError


def seed(db_path: Path) -> dict[str, int]:
    """建一个只含赛季、英雄、装备的最小库，供存档逻辑校验使用。"""
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)
        ids = {}
        for index, name in enumerate(("S18", "S19"), start=1):
            connection.execute(
                """
                INSERT INTO seasons (
                    mode, season, version, name, version_start_time, fetched_at,
                    hero_source_url, trait_source_url, hero_sha256, trait_sha256
                ) VALUES ('8', ?, '1.0.0', ?, '2026-01-01', '2026-01-01', '', '', '', '')
                """,
                (name, name),
            )
            ids[name] = connection.execute(
                "SELECT id FROM seasons WHERE season = ?", (name,)
            ).fetchone()["id"]

        for season in ("S18", "S19"):
            for key in ("hero_a", "hero_b"):
                connection.execute(
                    """
                    INSERT INTO heroes (
                        season_id, hero_key, name, cost, skill_values_json, stats_json,
                        origin_ids_json, map_ids_json, raw_json
                    ) VALUES (?, ?, ?, 1, '[]', '{}', '[]', '[]', '{}')
                    """,
                    (ids[season], f"{season}_{key}", key),
                )
                ids[f"{season}_{key}"] = connection.execute(
                    "SELECT id FROM heroes WHERE season_id = ? AND hero_key = ?",
                    (ids[season], f"{season}_{key}"),
                ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO equipment (season_id, equipment_key, name, type, raw_json)
                VALUES (?, ?, '巨人腰带', '基础装备', '{}')
                """,
                (ids[season], f"{season}_item"),
            )
    return ids


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = Path(self._dir.name) / "jcc.db"
        self.ids = seed(self.db)
        self.s18 = self.ids["S18"]
        self.hero_a = self.ids["S18_hero_a"]
        self.hero_b = self.ids["S18_hero_b"]

    def tearDown(self):
        self._dir.cleanup()

    def body(self, **overrides):
        payload = {
            "board": [self.hero_a, self.hero_b],
            "items": {str(self.hero_a): ["S18_item"]},
            "positions": {"3": self.hero_a},
        }
        payload.update(overrides)
        return payload

    def test_create_list_and_roundtrip(self):
        created = create_composition(self.db, self.s18, "主力阵容", "备注", 8, self.body())
        self.assertEqual(created["name"], "主力阵容")
        self.assertEqual(created["board_size"], 8)
        self.assertEqual(created["board"], [self.hero_a, self.hero_b])
        self.assertEqual(created["items"], {str(self.hero_a): ["S18_item"]})
        self.assertEqual(created["positions"], {"3": self.hero_a})

        listed = list_compositions(self.db, self.s18)
        self.assertEqual([item["id"] for item in listed], [created["id"]])

    def test_names_are_trimmed_and_unique_per_season(self):
        create_composition(self.db, self.s18, "  主力  ", "", 8, self.body())
        self.assertEqual(list_compositions(self.db, self.s18)[0]["name"], "主力")
        with self.assertRaises(DuplicateName):
            create_composition(self.db, self.s18, "主力", "", 8, self.body())
        # 换个赛季同名可以共存
        other = create_composition(
            self.db, self.ids["S19"], "主力", "", 8,
            {"board": [self.ids["S19_hero_a"]], "items": {}, "positions": {}},
        )
        self.assertEqual(other["name"], "主力")

    def test_season_isolation(self):
        create_composition(self.db, self.s18, "主力", "", 8, self.body())
        self.assertEqual(list_compositions(self.db, self.ids["S19"]), [])

    def test_rejects_cross_season_and_malformed_data(self):
        cases = {
            "跨赛季英雄": self.body(board=[self.ids["S19_hero_a"]], items={}, positions={}),
            "跨赛季装备": self.body(items={str(self.hero_a): ["S19_item"]}),
            "装备超过三件": self.body(
                items={str(self.hero_a): ["S18_item"] * 4}
            ),
            "装备给了场外英雄": self.body(items={"99999": ["S18_item"]}),
            "站位给了场外英雄": self.body(positions={"3": 99999}),
            "格子越界": self.body(positions={"99": self.hero_a}),
            "一人多格": self.body(positions={"1": self.hero_a, "2": self.hero_a}),
        }
        for label, payload in cases.items():
            with self.subTest(label), self.assertRaises(InvalidComposition):
                create_composition(self.db, self.s18, label, "", 8, payload)

        with self.assertRaises(InvalidComposition):
            create_composition(self.db, self.s18, "", "", 8, self.body())
        with self.assertRaises(InvalidComposition):
            create_composition(self.db, self.s18, "人口越界", "", 99, self.body())
        with self.assertRaises(InvalidComposition):
            create_composition(self.db, self.s18, "超出人口", "", 1, self.body())
        with self.assertRaises(InvalidComposition):
            create_composition(self.db, 999, "赛季不存在", "", 8, self.body())

    def test_update_name_only_keeps_board(self):
        created = create_composition(self.db, self.s18, "主力", "", 8, self.body())
        renamed = update_composition(self.db, self.s18, created["id"], name="备用")
        self.assertEqual(renamed["name"], "备用")
        self.assertEqual(renamed["board"], created["board"])
        self.assertEqual(renamed["positions"], created["positions"])
        self.assertEqual(renamed["board_size"], 8)

    def test_update_overwrites_board(self):
        created = create_composition(self.db, self.s18, "主力", "", 8, self.body())
        updated = update_composition(
            self.db, self.s18, created["id"],
            board_size=6,
            payload={"board": [self.hero_b], "items": {}, "positions": {}},
        )
        self.assertEqual(updated["board"], [self.hero_b])
        self.assertEqual(updated["board_size"], 6)
        self.assertEqual(updated["items"], {})
        self.assertEqual(updated["name"], "主力")

    def test_update_requires_size_with_payload(self):
        created = create_composition(self.db, self.s18, "主力", "", 8, self.body())
        with self.assertRaises(InvalidComposition):
            update_composition(
                self.db, self.s18, created["id"],
                payload={"board": [self.hero_b], "items": {}, "positions": {}},
            )

    def test_update_and_delete_respect_season(self):
        created = create_composition(self.db, self.s18, "主力", "", 8, self.body())
        with self.assertRaises(CompositionNotFound):
            update_composition(self.db, self.ids["S19"], created["id"], name="偷改")
        with self.assertRaises(CompositionNotFound):
            delete_composition(self.db, self.ids["S19"], created["id"])
        delete_composition(self.db, self.s18, created["id"])
        self.assertEqual(list_compositions(self.db, self.s18), [])
        with self.assertRaises(CompositionNotFound):
            delete_composition(self.db, self.s18, created["id"])

    def test_delete_season_cascades(self):
        created = create_composition(self.db, self.s18, "主力", "", 8, self.body())
        with connect(self.db) as connection:
            connection.execute("DELETE FROM seasons WHERE id = ?", (self.s18,))
        self.assertEqual(list_compositions(self.db, self.s18), [])
        self.assertTrue(created["id"])

    def test_ensure_schema_upgrades_old_database(self):
        old = Path(self._dir.name) / "old.db"
        with connect(old) as connection:
            connection.execute("CREATE TABLE seasons (id INTEGER PRIMARY KEY)")
        with connect(old) as connection:
            ensure_schema(connection)
            tables = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertIn("compositions", tables)

    def test_integrity_after_writes(self):
        create_composition(self.db, self.s18, "主力", "", 8, self.body())
        with connect(self.db) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            stored = connection.execute("SELECT payload_json FROM compositions").fetchone()[0]
        self.assertEqual(json.loads(stored)["board"], [self.hero_a, self.hero_b])

    def test_connect_closes_connection_after_context(self):
        with connect(self.db) as connection:
            connection.execute("SELECT 1").fetchone()
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            connection.execute("SELECT 1")

    def test_rejects_large_same_version_data_shrink(self):
        with connect(self.db) as connection:
            for index in range(10):
                connection.execute(
                    """
                    INSERT INTO heroes (
                        season_id, hero_key, name, cost, skill_values_json, stats_json,
                        origin_ids_json, map_ids_json, raw_json
                    ) VALUES (?, ?, ?, 1, '[]', '{}', '[]', '[]', '{}')
                    """,
                    (self.s18, f"extra_{index}", f"额外英雄{index}"),
                )
            with self.assertRaises(CollectionError):
                _reject_suspicious_shrink(
                    connection,
                    self.s18,
                    {"heroes": 1, "traits": 0, "equipment": 1, "augments": 0},
                )


if __name__ == "__main__":
    unittest.main()
