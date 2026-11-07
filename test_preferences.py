from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from export_review import filled_exif_count
from preferences import (Preferences, best_parameters, parse_date, preferred_date,
                         quality_values, recommendations, recommendation_reasons)


def photo(name, **values):
    return dict(path=name, stored_width=100, stored_height=200, **values)


class PreferencesTests(unittest.TestCase):
    def test_heic_overrides_other_advantages_but_detected_format_overrides_suffix(self):
        a = photo("small.heic", detected_format="HEIF")
        b = dict(a, path="large.jpg", detected_format="JPEG", stored_width=1000,
                 camera="iPhone", exif_tags=100, captured_at="2010:01:01 12:00:00")
        self.assertEqual(recommendations([a, b], Preferences()), {a["path"]})
        renamed = dict(b, path="misnamed.heic")
        self.assertEqual(quality_values(renamed, Preferences())["heic"], 0)
        self.assertEqual(quality_values(photo("legacy.HEIC"), Preferences())["heic"], 1)

    def test_reordering_and_disabling_change_advice(self):
        a = photo("a.heic")
        b = dict(photo("b.jpg"), stored_width=1000)
        settings = Preferences()
        settings.rules[0], settings.rules[1] = settings.rules[1], settings.rules[0]
        self.assertEqual(recommendations([a, b], settings), {b["path"]})
        settings = Preferences()
        settings.rules[0]["enabled"] = False
        self.assertEqual(recommendations([a, b], settings), {b["path"]})
        for rule in settings.rules:
            rule["enabled"] = False
        self.assertFalse(recommendations([a, b], settings))

    def test_earlier_date_then_camera_then_filled_exif_break_ties(self):
        settings = Preferences()
        a = photo("a.jpg", captured_at="2020:01:01 00:00:00", exif_tags=2)
        b = photo("b.jpg", captured_at="2021-01-01T00:00:00", camera="Camera", exif_tags=100)
        self.assertEqual(recommendations([a, b], settings), {a["path"]})
        b["captured_at"] = a["captured_at"]
        self.assertEqual(recommendations([a, b], settings), {b["path"]})
        a["camera"] = b["camera"]
        a["exif_filled_tags"], b["exif_filled_tags"] = 2, 1
        self.assertEqual(recommendations([a, b], settings), {a["path"]})

    def test_missing_invalid_and_offset_dates(self):
        settings = Preferences()
        row = photo("a.jpg", captured_at="0000:00:00 00:00:00", created_at="2020-03-04T12:30:45")
        self.assertEqual(preferred_date(row, settings), (row["created_at"], "created_at"))
        settings.date_source = "captured_at"
        self.assertEqual(preferred_date(row, settings), (None, None))
        other = photo("b.jpg", captured_at="2021:03:04 12:30:45")
        self.assertEqual(recommendations([row, other], settings), {other["path"]})
        self.assertEqual(parse_date("2020:03:04 12:30:45+03:00"), parse_date("2020-03-04T09:30:45Z"))

    def test_equal_files_remain_ties_and_do_not_highlight_parameters(self):
        rows = [photo("a.jpg"), photo("b.jpg")]
        self.assertEqual(recommendations(rows, Preferences()), {r["path"] for r in rows})
        self.assertTrue(all(not winners for winners in best_parameters(rows, Preferences()).values()))

    def test_highlighting_uses_each_criterion_independently(self):
        a = photo("a.heic", camera="Camera")
        b = dict(photo("b.jpg", exif_tags=20), stored_width=1000)
        winners = best_parameters([a, b], Preferences())
        self.assertEqual(winners["heic"], {a["path"]})
        self.assertEqual(winners["camera"], {a["path"]})
        self.assertEqual(winners["resolution"], {b["path"]})
        self.assertEqual(winners["exif"], {b["path"]})

    def test_preferences_persist_cpu_order_enabled_rules_and_display_options(self):
        with tempfile.TemporaryDirectory(prefix="v2_preferences_") as folder:
            filename = Path(folder) / "state" / "settings.json"
            settings = Preferences(cpu_percent=15, date_source="modified_at", highlight_best=False, suggest_best=False)
            settings.rules.reverse()
            settings.rules[1]["enabled"] = False
            settings.save(filename)
            self.assertEqual(asdict(Preferences.load(filename)), asdict(settings))
            filename.write_text("invalid JSON", encoding="utf-8")
            self.assertEqual(asdict(Preferences.load(filename)), asdict(Preferences()))

    def test_empty_exif_is_not_counted_and_zero_is_valid(self):
        count = filled_exif_count({272: "Camera", 34665: 100, 400: "  ", 401: b"\x00", 402: None},
                                  {36867: "2020:01:01 00:00:00", 3: (), 4: 0})
        self.assertEqual(count, 3)

    def test_removal_folder_and_exclusive_mode_persist_with_safe_legacy_defaults(self):
        with tempfile.TemporaryDirectory(prefix="v2_removal_preferences_") as folder:
            root = Path(folder).resolve()
            settings = Preferences(delete_mode="folder", delete_folder=str(root))
            settings.save(root/"settings.json")
            restored = Preferences.load(root/"settings.json")
            self.assertEqual((restored.delete_mode, restored.delete_folder), ("folder", str(root)))
            self.assertEqual(Preferences.from_dict({}).delete_mode, "recycle_bin")
            self.assertEqual(Preferences.from_dict({"delete_mode": "folder", "delete_folder": "relative"}).delete_mode, "recycle_bin")

    def test_reasons_follow_rule_order_and_show_both_values(self):
        a = photo("a.heic", camera=None)
        b = dict(photo("b.jpg", camera="Camera"), stored_width=1000)
        settings = Preferences()
        for expected in ("heic", "resolution"):
            reasons = recommendation_reasons([a, b], settings)
            self.assertEqual({r["rule"] for r in reasons.values()}, {expected})
            winner = next(iter(recommendations([a, b], settings)))
            self.assertIn("Рекомендуем", reasons[winner]["text"])
            self.assertIn("Уступает", reasons[next(p for p in reasons if p != winner)]["text"])
            settings.rules[0], settings.rules[1] = settings.rules[1], settings.rules[0]

    def test_reasons_do_not_recommend_either_card_when_group_has_a_better_file(self):
        a, b = photo("a.jpg"), dict(photo("b.jpg"), stored_width=200)
        winner = dict(photo("group_winner.jpg"), stored_width=1000)
        reasons = recommendation_reasons([a, b, winner], Preferences(), [a["path"], b["path"]])
        self.assertEqual(set(reasons), {a["path"], b["path"]})
        for reason in reasons.values():
            self.assertIn("Уступает в группе", reason["text"])
            self.assertIn("group_winner.jpg", reason["detail"])

    def test_reasons_for_tied_winners_compare_with_a_losing_group_member(self):
        rows = [photo("a.jpg"), photo("b.jpg"), dict(photo("c.jpg"), stored_width=50)]
        reasons = recommendation_reasons(rows, Preferences(), ["a.jpg", "b.jpg"])
        for reason in reasons.values():
            self.assertIn("Один из лучших", reason["text"])
            self.assertIn("c.jpg", reason["detail"])
            self.assertEqual(reason["rule"], "resolution")

    def test_reasons_distinguish_exif_ties_from_windows_fallback_and_mixed_sources(self):
        a = photo("a.jpg", captured_at="2015:08:15 12:37:31", created_at="2016-01-10T14:49:27")
        b = photo("b.jpg", captured_at=a["captured_at"], created_at="2018-02-27T21:46:21")
        settings = Preferences()
        reasons = recommendation_reasons([a, b], settings)
        self.assertTrue(all(r["rule"] is None and "Равноценны" in r["text"] for r in reasons.values()))
        a["captured_at"] = b["captured_at"] = None
        reasons = recommendation_reasons([a, b], settings)
        for reason in reasons.values():
            self.assertEqual(reason["rule"], "date")
            self.assertIn("создан, Windows", reason["text"])
            self.assertIn("не дата съёмки", reason["text"])
        a["captured_at"] = "2015:08:15 12:37:31"
        reasons = recommendation_reasons([a, b], settings)
        self.assertTrue(all("разные источники" in r["detail"] for r in reasons.values()))

    def test_reasons_handle_disabled_rules_single_file_and_unknown_dates(self):
        rows = [photo("a.jpg"), photo("b.jpg", captured_at="2020-01-01T00:00:00")]
        self.assertIn("дата отсутствует", recommendation_reasons(rows, Preferences())["a.jpg"]["text"])
        for settings in (Preferences(suggest_best=False), Preferences(rules=[])):
            self.assertTrue(all(r["rule"] is None for r in recommendation_reasons(rows, settings).values()))
        reason = recommendation_reasons(rows[:1], Preferences())["a.jpg"]
        self.assertIn("Нет других", reason["text"])


if __name__ == "__main__":
    unittest.main()
