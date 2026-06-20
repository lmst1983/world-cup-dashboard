import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_standings",
    ROOT / "scripts" / "update_standings.py",
)
UPDATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(UPDATE)


class UpdateStandingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = json.loads((ROOT / "data" / "standings.json").read_text(encoding="utf-8"))

    def test_rebuilds_group_from_finished_matches(self):
        payload = {
            "matches": [
                {
                    "id": 1,
                    "status": "FINISHED",
                    "stage": "GROUP_G",
                    "homeTeam": {"name": "Iran"},
                    "awayTeam": {"name": "Belgium"},
                    "score": {"fullTime": {"home": 1, "away": 0}},
                },
                {
                    "id": 2,
                    "status": "FINISHED",
                    "group": "GROUP_G",
                    "homeTeam": {"name": "New Zealand"},
                    "awayTeam": {"name": "Egypt"},
                    "score": {"fullTime": {"home": 0, "away": 2}},
                },
            ]
        }

        result = UPDATE.build_dashboard(
            self.seed,
            payload,
            enforce_progress=False,
            updated_at="2026-06-21T12:00:00Z",
        )
        group_g = next(group for group in result["groups"] if group["id"] == "G")

        self.assertEqual([team["english"] for team in group_g["teams"]], [
            "Egypt",
            "IR Iran",
            "Belgium",
            "New Zealand",
        ])
        self.assertEqual(result["summary"]["playedMatches"], 2)
        self.assertEqual(result["summary"]["goals"], 3)
        self.assertEqual(result["summary"]["averageGoals"], 1.5)

    def test_ignores_unfinished_and_knockout_matches(self):
        payload = {
            "matches": [
                {
                    "id": 10,
                    "status": "TIMED",
                    "stage": "GROUP_A",
                    "homeTeam": {"name": "Mexico"},
                    "awayTeam": {"name": "South Africa"},
                    "score": {"fullTime": {"home": None, "away": None}},
                },
                {
                    "id": 11,
                    "status": "FINISHED",
                    "stage": "LAST_32",
                    "homeTeam": {"name": "Mexico"},
                    "awayTeam": {"name": "United States"},
                    "score": {"fullTime": {"home": 1, "away": 0}},
                },
            ]
        }

        result = UPDATE.build_dashboard(self.seed, payload, enforce_progress=False)
        self.assertEqual(result["summary"]["playedMatches"], 0)
        self.assertEqual(result["summary"]["goals"], 0)

    def test_rejects_unknown_team(self):
        payload = {
            "matches": [
                {
                    "id": 20,
                    "status": "FINISHED",
                    "stage": "GROUP_A",
                    "homeTeam": {"name": "Unknown FC"},
                    "awayTeam": {"name": "Mexico"},
                    "score": {"fullTime": {"home": 0, "away": 1}},
                }
            ]
        }

        with self.assertRaisesRegex(UPDATE.UpdateError, "无法识别"):
            UPDATE.build_dashboard(self.seed, payload, enforce_progress=False)

    def test_timestamp_only_is_not_a_meaningful_change(self):
        newer = json.loads(json.dumps(self.seed))
        newer["updatedAt"] = "2026-06-21T12:00:00Z"
        self.assertFalse(UPDATE.has_meaningful_change(self.seed, newer))


if __name__ == "__main__":
    unittest.main()
