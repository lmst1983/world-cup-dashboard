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

STAGE_LABELS = {
    "GROUP": "小组赛",
    "LAST_32": "32强淘汰赛",
    "ROUND_OF_32": "32强淘汰赛",
    "LAST_16": "16强",
    "ROUND_OF_16": "16强",
    "QUARTER_FINALS": "1/4决赛",
    "SEMI_FINALS": "半决赛",
    "THIRD_PLACE": "季军赛",
    "FINAL": "决赛",
}

STAGE_ORDER = {
    "GROUP": 10,
    "LAST_32": 20,
    "ROUND_OF_32": 20,
    "LAST_16": 30,
    "ROUND_OF_16": 30,
    "QUARTER_FINALS": 40,
    "SEMI_FINALS": 50,
    "THIRD_PLACE": 60,
    "FINAL": 70,
}

KNOCKOUT_STAGES = (
    "LAST_32",
    "ROUND_OF_32",
    "LAST_16",
    "ROUND_OF_16",
    "QUARTER_FINALS",
    "SEMI_FINALS",
    "THIRD_PLACE",
    "FINAL",
)

STATUS_LABELS = {
    "TIMED": "未开始",
    "SCHEDULED": "已排期",
    "IN_PLAY": "进行中",
    "PAUSED": "中场",
    "FINISHED": "已完赛",
    "AWARDED": "已判定",
    "POSTPONED": "延期",
    "SUSPENDED": "暂停",
    "CANCELLED": "取消",
}

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


def normalize_stage(value: Any) -> str:
    normalized = str(value or "").upper().replace("-", "_").replace(" ", "_")
    stage_aliases = {
        "GROUP_STAGE": "GROUP",
        "ROUND_OF_32": "LAST_32",
        "ROUND_OF_16": "LAST_16",
        "QUARTER_FINAL": "QUARTER_FINALS",
        "SEMI_FINAL": "SEMI_FINALS",
        "THIRD_PLACE_PLAYOFF": "THIRD_PLACE",
        "PLAY_OFF_FOR_THIRD_PLACE": "THIRD_PLACE",
    }
    return stage_aliases.get(normalized, normalized)


def extract_stage(match: dict[str, Any]) -> tuple[str, str, int, str | None]:
    group_id = extract_group_id(match)
    stage_id = normalize_stage(match.get("stage"))

    if group_id:
        return "GROUP", f"{group_id}组", STAGE_ORDER["GROUP"], group_id
    if stage_id in STAGE_LABELS:
        return stage_id, STAGE_LABELS[stage_id], STAGE_ORDER.get(stage_id, 90), None
    if stage_id:
        return stage_id, stage_id.replace("_", " ").title(), STAGE_ORDER.get(stage_id, 90), None
    return "UNKNOWN", "待定阶段", 99, None


def extract_score(match: dict[str, Any]) -> tuple[int, int] | None:
    score = match.get("score") or {}
    for key in ("fullTime", "regularTime"):
        value = score.get(key) or {}
        home = value.get("home")
        away = value.get("away")
        if isinstance(home, int) and isinstance(away, int):
            return home, away
    return None


def extract_penalty_score(match: dict[str, Any]) -> tuple[int, int] | None:
    score = match.get("score") or {}
    value = score.get("penalties") or {}
    home = value.get("home")
    away = value.get("away")
    if isinstance(home, int) and isinstance(away, int):
        return home, away
    return None


def raw_team_name(team_payload: dict[str, Any]) -> str:
    for key in ("name", "shortName", "tla"):
        value = team_payload.get(key)
        if value:
            return str(value)
    return "待定"


def team_identity(
    team_payload: dict[str, Any],
    aliases: dict[str, str],
    metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    try:
        canonical = resolve_team(team_payload, aliases)
    except UpdateError:
        display_name = raw_team_name(team_payload)
        return {
            "name": display_name,
            "english": display_name,
            "flag": "🏳️",
            "colorA": "#61727a",
            "colorB": "#24323c",
        }

    return {
        key: metadata[canonical][key]
        for key in ("name", "english", "flag", "colorA", "colorB")
    }


def score_winner_name(match: dict[str, Any], home: dict[str, Any], away: dict[str, Any]) -> str | None:
    winner = (match.get("score") or {}).get("winner")
    if winner == "HOME_TEAM":
        return str(home["name"])
    if winner == "AWAY_TEAM":
        return str(away["name"])
    return None


def match_sort_key(match: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(match.get("stageOrder", 99)),
        str(match.get("utcDate") or "9999-12-31T23:59:59Z"),
        str(match.get("id") or ""),
    )


def build_match_schedule(
    raw_matches: list[dict[str, Any]],
    aliases: dict[str, str],
    metadata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_match in raw_matches:
        stage_id, stage_label, stage_order, group_id = extract_stage(raw_match)
        home = team_identity(raw_match.get("homeTeam") or {}, aliases, metadata)
        away = team_identity(raw_match.get("awayTeam") or {}, aliases, metadata)
        score = extract_score(raw_match)
        penalties = extract_penalty_score(raw_match)
        match_id = str(
            raw_match.get("id")
            or f"{stage_id}:{home['english']}:{away['english']}:{raw_match.get('utcDate', '')}"
        )
        if match_id in seen:
            continue
        seen.add(match_id)

        schedule.append(
            {
                "id": match_id,
                "utcDate": raw_match.get("utcDate"),
                "status": raw_match.get("status") or "UNKNOWN",
                "statusLabel": STATUS_LABELS.get(str(raw_match.get("status") or ""), "状态未知"),
                "stage": stage_id,
                "stageLabel": stage_label,
                "stageOrder": stage_order,
                "group": group_id,
                "matchday": raw_match.get("matchday"),
                "homeTeam": home,
                "awayTeam": away,
                "score": {
                    "home": score[0] if score else None,
                    "away": score[1] if score else None,
                    "penaltiesHome": penalties[0] if penalties else None,
                    "penaltiesAway": penalties[1] if penalties else None,
                },
                "winner": score_winner_name(raw_match, home, away),
            }
        )

    return sorted(schedule, key=match_sort_key)


def find_next_match(schedule: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        match
        for match in schedule
        if match.get("status") not in FINISHED_STATUSES
        and match.get("status") not in {"CANCELLED", "SUSPENDED"}
    ]
    candidates.sort(key=lambda match: str(match.get("utcDate") or "9999-12-31T23:59:59Z"))
    return candidates[0] if candidates else None


def build_knockout(schedule: list[dict[str, Any]]) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    seen_rounds: set[str] = set()

    for stage_id in KNOCKOUT_STAGES:
        if stage_id in seen_rounds:
            continue
        stage_matches = [match for match in schedule if match["stage"] == stage_id]
        if not stage_matches:
            continue
        seen_rounds.add(stage_id)
        rounds.append(
            {
                "id": stage_id,
                "label": STAGE_LABELS.get(stage_id, stage_id),
                "matches": sorted(stage_matches, key=match_sort_key),
            }
        )

    return {"rounds": rounds}


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
    group_goals = 0

    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list):
        raise UpdateError("接口响应缺少 matches 数组")

    schedule = build_match_schedule(raw_matches, aliases, metadata)

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
        group_goals += home_goals + away_goals
        group_matches[group_id].append(
            {
                "home": home,
                "away": away,
                "homeGoals": home_goals,
                "awayGoals": away_goals,
            }
        )

    group_played_matches = len(seen_matches)
    previous_summary = seed.get("summary", {})
    previous_group_played = int(previous_summary.get("groupPlayedMatches", previous_summary.get("playedMatches", 0)))
    if enforce_progress and group_played_matches < previous_group_played:
        raise UpdateError(
            f"接口仅返回 {group_played_matches} 场已完赛小组赛，少于当前文件的 {previous_group_played} 场；已拒绝覆盖"
        )
    if group_played_matches > 72:
        raise UpdateError(f"小组赛场次数异常: {group_played_matches}")
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

    finished_schedule = [
        match
        for match in schedule
        if match["status"] in FINISHED_STATUSES
        and isinstance(match["score"]["home"], int)
        and isinstance(match["score"]["away"], int)
    ]
    played_matches = len(finished_schedule)
    total_goals = sum(match["score"]["home"] + match["score"]["away"] for match in finished_schedule)
    result_set = payload.get("resultSet") if isinstance(payload.get("resultSet"), dict) else {}
    total_matches = int(result_set.get("count") or len(schedule) or previous_summary.get("totalMatches", 0))

    timestamp = updated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": 2,
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
            "totalMatches": total_matches,
            "goals": total_goals,
            "averageGoals": round(total_goals / played_matches, 2) if played_matches else 0,
            "groupPlayedMatches": group_played_matches,
            "groupTotalMatches": 72,
            "groupGoals": group_goals,
            "knockoutPlayedMatches": max(0, played_matches - group_played_matches),
        },
        "groups": output_groups,
        "matches": schedule,
        "nextMatch": find_next_match(schedule),
        "knockout": build_knockout(schedule),
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
    keys = ("source", "summary", "groups", "matches", "nextMatch", "knockout")
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
                f"数据无变化: {dashboard['summary']['playedMatches']} 场，"
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
