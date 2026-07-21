from datetime import date
from pathlib import Path
import tempfile
import unittest

from jcc.collector import (
    CollectionError,
    SeasonVersion,
    parse_picture_template,
    select_version,
    transform_augments,
    transform_equipment,
    transform_heroes,
    transform_traits,
)
from jcc.images import _image_extension, _valid_image, attach_cached_hero_images


VERSION = SeasonVersion("8", "S18", "1.2.0", "测试赛季", "2026-01-01", "/chess.js", "/trait.js")


class CollectorTests(unittest.TestCase):
    def test_image_validation_and_extension(self):
        self.assertTrue(_valid_image(b"\x89PNG\r\n\x1a\ncontent"))
        self.assertTrue(_valid_image(b"\xff\xd8\xffcontent"))
        self.assertFalse(_valid_image(b"<html>not an image</html>"))
        self.assertEqual(_image_extension("https://example.test/hero.png?v=1"), ".png")

    def test_parse_full_picture_template(self):
        config = '''
        var baseUrlManager = {
          hero_pic_big:{
            "8_S18":"//game.gtimg.cn/images/jk/jkimg/mode8s18/1624x750/{{pic_name}}.jpg"
          },
          hero_pic_long:{}
        }
        '''
        template = parse_picture_template(config, "8", "S18")
        self.assertEqual(
            template,
            "https://game.gtimg.cn/images/jk/jkimg/mode8s18/1624x750/{{pic_name}}.jpg",
        )

    def test_attach_cached_hero_image(self):
        heroes = [{"hero_key": "1234", "picture": "https://example.test/a.png"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "heroes" / VERSION.key / "1234.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
            counts = attach_cached_hero_images(heroes, VERSION, root)
        self.assertEqual(counts["cached"], 1)
        self.assertEqual(heroes[0]["local_picture"], f"heroes/{VERSION.key}/1234.png")

    def test_select_version_ignores_future_release(self):
        records = [
            {"mode": "8", "season": "S18", "version": "1.1.0", "version_start_time": "2026-01-01", "herourl": "/a", "traiturl": "/b", "equipurl": "/e", "hexurl": "/f", "name": "A"},
            {"mode": "8", "season": "S18", "version": "1.2.0", "version_start_time": "2026-02-01", "herourl": "/c", "traiturl": "/d", "equipurl": "/g", "hexurl": "/h", "name": "B"},
        ]
        selected = select_version(records, "8", "S18", today=date(2026, 1, 15))
        self.assertEqual(selected.version, "1.1.0")

    def test_select_version_rejects_future_release_without_override(self):
        records = [
            {
                "mode": "8", "season": "S18", "version": "2.0.0",
                "version_start_time": "2099-01-01", "herourl": "/a",
                "traiturl": "/b", "equipurl": "/e", "hexurl": "/f",
            }
        ]
        with self.assertRaises(CollectionError):
            select_version(records, "8", "S18", today=date(2026, 1, 15))
        selected = select_version(
            records, "8", "S18", requested_version="2.0.0", today=date(2026, 1, 15)
        )
        self.assertEqual(selected.version, "2.0.0")

    def test_transform_official_star_records_and_traits(self):
        hero_base = {
            "name": "测试英雄", "setid": "8", "heroType": "0", "showHeroTag": "1",
            "sellPrice": "2", "species": "10", "class": "20", "heroPaint": "hero",
            "picture": "https://example/hero/a.png", "skillName": "技能", "skillDesc": "说明",
            "skillIcon": "https://example/skill.png", "skillValueDesc": "1/2/3",
            "armor": "30", "attackRange": "1", "attackSpeed": "0.7",
            "criticalStrikeChance": "25", "initAttackDamage": "50", "initHP": "600",
            "initMP": "0", "magicResist": "30", "maxMP": "60", "mapID": "1",
        }
        second_star = {**hero_base, "initHP": "1080", "mapID": "2"}
        heroes = transform_heroes({"setId": 8, "data": {"11234": hero_base, "21234": second_star}}, VERSION)
        self.assertEqual(len(heroes), 1)
        self.assertEqual(heroes[0]["hero_key"], "1234")
        self.assertEqual(heroes[0]["stats"]["initHP"], ["600", "1080"])

        trait_payload = {
            "setId": 8,
            "data": {
                "1": {"id": 1, "setid": "8", "checkId": "10", "type": 0, "name": "种族", "color": "1", "num": "2", "level": "1", "realDesc": "效果", "desc2": "说明", "picture": "", "prefix": ""},
                "2": {"id": 2, "setid": "8", "checkId": "20", "type": 1, "name": "职业", "color": "1", "num": "2", "level": "1", "realDesc": "效果", "desc2": "说明", "picture": "", "prefix": ""},
            },
        }
        traits = transform_traits(trait_payload, VERSION)
        self.assertEqual(
            {(item["trait_key"], item["type"]) for item in traits},
            {("10", "race"), ("20", "job")},
        )

    def test_transform_equipment_and_augments(self):
        equipment = transform_equipment(
            {
                "setId": 8,
                "data": {
                    "1001": {
                        "id": "1001", "planID": "8", "name": "暴风之剑",
                        "type": "基础装备", "basicDesc": "+10攻击力", "desc": "",
                        "picture": "sword.png", "synthesis1": "0", "synthesis2": "0",
                        "fetterID": "", "EffectType": "0", "sort": "1",
                    },
                    "invalid": {"id": "0", "planID": "8", "type": "-1"},
                },
            },
            VERSION,
        )
        self.assertEqual(len(equipment), 1)
        self.assertEqual(equipment[0]["equipment_key"], "1001")
        self.assertIsNone(equipment[0]["component_1_key"])

        augments = transform_augments(
            {
                "setId": 8,
                "data": {
                    "100": {
                        "id": "100", "name": "测试符文", "level": "2",
                        "desc": "效果", "icon": "augment.png", "is_legend": 0,
                        "hero_enhancement_type": "0", "fetterId": "10", "fetterType": "1",
                    }
                },
            },
            VERSION,
        )
        self.assertEqual(len(augments), 1)
        self.assertEqual(augments[0]["level"], 2)
        self.assertEqual(augments[0]["fetter_id"], "10")


if __name__ == "__main__":
    unittest.main()
