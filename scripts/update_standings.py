#!/usr/bin/env python3
"""Fetch World Cup matches and rebuild the dashboard standings data."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = ROOT / "data" / "standings.json"
GROUP_IDS = tuple("ABCDEFGHIJKL")
FINISHED_STATUSES = {"FINISHED", "AWARDED"}

TEAM_ALIASES = {
    "usa": "United States",
    "united states of america": "United States",
    "us": "United States",
    "south korea": "Korea Republic",
    "korea rep": "Korea Republic",
    "republic of korea": "Korea Republic",
    "czech republic": "Czechia",
    "bosnia and herzegovina": "Bosnia & Herzegovina",
    "bosnia-herzegovina": "Bosnia & Herzegovina",
    "ivory coast": "Côte d'Ivoire",
    "cote d ivoire": "Côte d'Ivoire",
    "turkey": "Türkiye",
    "iran": "IR Iran",
    "iran islamic republic of": "IR Iran",
    "cape verde": "Cabo Verde",
    "curacao": "Curaçao",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of the congo": "DR Congo",
    "congo democratic republic": "DR Congo",
}


class UpdateError(RuntimeError):
    """Raised when remote data is incomplete or cannot be safely applied."""


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_text = ascii_text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"无法读取 JSON 文件 {path}: {exc}") from exc


def fetch_matches(token: str, competition: str, season: int, base_url: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"season": season})
    url = f"{base_url.rstrip('/')}/competitions/{competition}/matches?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "X-Auth-Token": token,
            "Accept": "application/json",
            "User-Agent": "world-cup-dashboard/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise UpdateError(f"数据接口返回 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpdateError(f"无法连接比赛数据接口: {exc}") from exc


def build_team_index(seed: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    metadata: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}

    for group in seed["groups"]:
        for team in group["teams"]:
            canonical = team["english"]
            metadata[canonical] = {
                "name": team["name"],
                "english": canonical,
                "flag": team["flag"],
                "colorA": team["colorA"],
                "colorB": team["colorB"],
                "group": group["id"],
                "seedRank": int(team.get("rank", 99)),
            }
            aliases[normalize_name(canonical)] = canonical

    for alias, canonical in TEAM_ALIASES.items():
        if canonical in metadata:
            aliases[normalize_name(alias)] = canonical

    return metadata, aliases


def resolve_team(team_payload: dict[str, Any], aliases: dict[str, str]) -> str:
    candidates = (
        team_payload.get("name"),
        team_payload.get("shortName"),
        team_payload.get("tla"),
    )

    for candidate in candidates:
        if candidate and normalize_name(str(candidate)) in aliases:
            return aliases[normalize_name(str(candidate))]

    display_name = next((str(item) for item in candidates if item), "未知球队")
    raise UpdateError(f"接口返回了无法识别的球队: {display_name}")


def extract_group_id(match: dict[str, Any]) -> str | None:
    for raw_value in (match.get("group"), match.get("stage")):
        if not raw_value:
            continue
        normalized = str(raw_value).upper().replace("-", "_").replace(" ", "_")
        found = re.fullmatch(r"(?:GROUP_?)?([A-L])", normalized)
        if found:
            return found.group(1)
    return None


def extract_score(match: dict[str, Any]) -> tuple[int, int] | None:
    score = match.get("score") or {}
    for key in ("fullTime", "regularTime"):
        value = score.get(key) or {}
        home = value.get("home")
        away = value.get("away")
        if isinstance(home, int) and isinstance(away, int):
            return home, away
    return None


def empty_stats() -> dict[str, int]:
    return {
        "played": 0,
        "won": 0,
        "draw": 0,
        "lost": 0,
        "goalsFor": 0,
        "goalsAgainst": 0,
        "goalDifference": 0,
        "points": 0,
    }


def apply_result(stats: dict[str, int], goals_for: int, goals_against: int) -> None:
    stats["played"] += 1
    stats["goalsFor"] += goals_for
    stats["goalsAgainst"] += goals_against
    stats["goalDifference"] = stats["goalsFor"] - stats["goalsAgainst"]

    if goals_for > goals_against:
        stats["won"] += 1
        stats["points"] += 3
    elif goals_for == goals_against:
        stats["draw"] += 1
        stats["points"] += 1
    else:
        stats["lost"] += 1


def head_to_head_key(
    team: str,
    tied_teams: set[str],
    matches: list[dict[str, Any]],
) -> tuple[int, int, int]:
    points = goals_for = goals_against = 0

    for match in matches:
        if team not in (match["home"], match["away"]):
            continue
        opponent = match["away"] if match["home"] == team else match["home"]
        if opponent not in tied_teams:
            continue

        if match["home"] == team:
            scored, conceded = match["homeGoals"], match["awayGoals"]
        else:
            scored, conceded = match["awayGoals"], match["homeGoals"]

        goals_for += scored
        goals_against += conceded
        points += 3 if scored > conceded else 1 if scored == conceded else 0

    return points, goals_for - goals_against, goals_for


def rank_group(
    team_names: list[str],
    stats: dict[str, dict[str, int]],
    matches: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
) -> list[str]:
    primary_buckets: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    for team_name in team_names:
        team_stats = stats[team_name]
        primary_buckets[
            (
                team_stats["points"],
                team_stats["goalDifference"],
                team_stats["goalsFor"],
            )
        ].append(team_name)

    ranked: list[str] = []
    for primary_key in sorted(primary_buckets, reverse=True):
        tied = primary_buckets[primary_key]
        tied_set = set(tied)
        tied.sort(
            key=lambda team_name: (
                *head_to_head_key(team_name, tied_set, matches),
                -metadata[team_name]["seedRank"],
            ),
            reverse=True,
        )
        ranked.extend(tied)
    return ranked


def build_dashboard(
    seed: dict[str, Any],
    payload: dict[str, Any],
    *,
    enforce_progress: bool = True,
    updated_at: str | None = None,
) -> dict[str, Any]:
    metadata, aliases = build_team_index(seed)
    stats = {team_name: empty_stats() for team_name in metadata}
    group_matches: dict[str, list[dict[str, Any]]] = {group_id: [] for group_id in GROUP_IDS}
    seen_matches: set[str] = set()
    total_goals = 0

    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list):
        raise UpdateError("接口响应缺少 matches 数组")

    for raw_match in raw_matches:
        if raw_match.get("status") not in FINISHED_STATUSES:
            continue

        group_id = extract_group_id(raw_match)
        if group_id not in group_matches:
            continue

        score = extract_score(raw_match)
        if score is None:
            raise UpdateError(f"已完赛场次缺少完整比分: {raw_match.get('id', 'unknown')}")

        home = resolve_team(raw_match.get("homeTeam") or {}, aliases)
        away = resolve_team(raw_match.get("awayTeam") or {}, aliases)
        if metadata[home]["group"] != group_id or metadata[away]["group"] != group_id:
            raise UpdateError(f"{home} 对 {away} 的小组标记与种子数据不一致")

        match_key = str(
            raw_match.get("id")
            or f"{group_id}:{home}:{away}:{raw_match.get('utcDate', '')}"
        )
        if match_key in seen_matches:
            continue
        seen_matches.add(match_key)

        home_goals, away_goals = score
        apply_result(stats[home], home_goals, away_goals)
        apply_result(stats[away], away_goals, home_goals)
        total_goals += home_goals + away_goals
        group_matches[group_id].append(
            {
                "home": home,
                "away": away,
                "homeGoals": home_goals,
                "awayGoals": away_goals,
            }
        )

    played_matches = len(seen_matches)
    previous_played = int(seed.get("summary", {}).get("playedMatches", 0))
    if enforce_progress and played_matches < previous_played:
        raise UpdateError(
            f"接口仅返回 {played_matches} 场已完赛小组赛，少于当前文件的 {previous_played} 场；已拒绝覆盖"
        )
    if played_matches > 72:
        raise UpdateError(f"小组赛场次数异常: {played_matches}")
    if any(team_stats["played"] > 3 for team_stats in stats.values()):
        raise UpdateError("至少一支球队的小组赛场次超过 3 场")

    output_groups: list[dict[str, Any]] = []
    for seed_group in seed["groups"]:
        group_id = seed_group["id"]
        team_names = [team["english"] for team in seed_group["teams"]]
        ranked_names = rank_group(team_names, stats, group_matches[group_id], metadata)
        output_teams = []

        for rank, team_name in enumerate(ranked_names, start=1):
            output_teams.append(
                {
                    "rank": rank,
                    **{key: metadata[team_name][key] for key in ("name", "english", "flag")},
                    **stats[team_name],
                    **{key: metadata[team_name][key] for key in ("colorA", "colorB")},
                }
            )
        output_groups.append({"id": group_id, "teams": output_teams})

    timestamp = updated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": 1,
        "competition": "FIFA World Cup 2026",
        "updatedAt": timestamp,
        "source": {
            "id": "football-data",
            "label": "football-data.org 自动同步",
            "automatic": True,
        },
        "summary": {
            "teams": len(metadata),
            "playedMatches": played_matches,
            "totalMatches": 72,
            "goals": total_goals,
            "averageGoals": round(total_goals / played_matches, 2) if played_matches else 0,
        },
        "groups": output_groups,
    }


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(serialized)
        temp_path = Path(handle.name)

    temp_path.replace(path)


def has_meaningful_change(current: dict[str, Any], updated: dict[str, Any]) -> bool:
    keys = ("source", "summary", "groups")
    return any(current.get(key) != updated.get(key) for key in keys)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="从本地 football-data.org 响应文件读取，便于测试")
    parser.add_argument("--seed", type=Path, default=DEFAULT_DATA_PATH, help="当前积分与球队元数据文件")
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA_PATH, help="更新后的输出文件")
    parser.add_argument("--dry-run", action="store_true", help="只校验并输出摘要，不写文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    seed = load_json(args.seed)

    if args.input:
        payload = load_json(args.input)
    else:
        token = os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()
        if not token:
            raise UpdateError("缺少 FOOTBALL_DATA_TOKEN 环境变量")
        payload = fetch_matches(
            token=token,
            competition=os.environ.get("FOOTBALL_DATA_COMPETITION", "WC"),
            season=int(os.environ.get("FOOTBALL_DATA_SEASON", "2026")),
            base_url=os.environ.get("FOOTBALL_DATA_BASE_URL", "https://api.football-data.org/v4"),
        )

    dashboard = build_dashboard(seed, payload)
    if args.dry_run:
        print(json.dumps(dashboard["summary"], ensure_ascii=False))
    else:
        current_output = load_json(args.output) if args.output.exists() else {}
        if not has_meaningful_change(current_output, dashboard):
            print(
                f"积分无变化: {dashboard['summary']['playedMatches']} 场，"
                f"{dashboard['summary']['goals']} 球"
            )
            return 0
        atomic_write_json(args.output, dashboard)
        print(
            f"已更新 {args.output}: "
            f"{dashboard['summary']['playedMatches']} 场，"
            f"{dashboard['summary']['goals']} 球，"
            f"{dashboard['updatedAt']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as error:
        print(f"更新失败: {error}")
        raise SystemExit(1)
