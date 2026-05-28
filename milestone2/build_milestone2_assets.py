from __future__ import annotations

from datetime import datetime, UTC
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for _p in (str(ROOT), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from second_degree_utils import (
    build_ego_case,
    choose_exemplar_users,
    friend_attribute_match_rates,
    load_second_degree_assets,
    summarize_second_degree,
)


OUTPUT_DIR = Path(__file__).resolve().parent / "data"
OUTPUT_PATH = OUTPUT_DIR / "analysis_summary.json"
PROTOTYPE_DATA_PATH = Path(__file__).resolve().parent / "prototype" / "data.js"


def round_float(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def series_to_pairs(series: pd.Series, key_name: str = "label") -> list[dict[str, int | float | str]]:
    output: list[dict[str, int | float | str]] = []
    for index, value in series.items():
        if pd.isna(index):
            label = "unknown"
        else:
            label = str(index)
        numeric_value = float(value)
        output.append(
            {
                key_name: label,
                "value": int(numeric_value) if numeric_value.is_integer() else round_float(numeric_value, 3),
            }
        )
    return output


def histogram_from_bins(series: pd.Series, bins: list[int]) -> list[dict[str, int | str]]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    results: list[dict[str, int | str]] = []
    for start, end in zip(bins[:-1], bins[1:], strict=False):
        count = int(values.loc[(values >= start) & (values < end)].shape[0])
        results.append({"label": f"{start}-{end - 1}", "value": count})
    results.append({"label": f"{bins[-1]}+", "value": int(values.loc[values >= bins[-1]].shape[0])})
    return results


def top_country_counts(users: pd.DataFrame) -> pd.Series:
    return users["country"].fillna("UNKNOWN").value_counts().head(10)


def build_payload() -> dict[str, object]:
    assets = load_second_degree_assets(ROOT / "data")
    users = assets.users
    network = assets.network

    second_degree_summary = summarize_second_degree(users, assets.adjacency)
    friend_match = friend_attribute_match_rates(users, assets.adjacency)
    exemplar_ids = choose_exemplar_users(users, assets.adjacency, summary=second_degree_summary, max_cases=3)
    exemplar_cases = []
    for uid in exemplar_ids:
        case = build_ego_case(uid, users, assets.adjacency, top_n_candidates=16)
        large = build_ego_case(uid, users, assets.adjacency, top_n_candidates=50)
        case["finder_candidates"] = large["candidates"]
        exemplar_cases.append(case)

    degree_summary = users["degree"].describe(percentiles=[0.5, 0.9, 0.99])
    second_summary = second_degree_summary["second_degree_count"].describe(percentiles=[0.5, 0.9, 0.99])
    both_summary = second_degree_summary["shared_both_second_degree"].describe(percentiles=[0.5, 0.9, 0.99])

    age_group_distribution = users["age_group"].fillna("unknown").value_counts().reindex(
        ["10s", "20s", "30s", "40s", "50s", "60+", "unknown"],
        fill_value=0,
    )
    country_counts = top_country_counts(users)

    caveats = [
        "The local workspace contains user demographics and the friendship graph, so age-group and country matching can be implemented directly.",
        "The local workspace does not contain a user-to-artist listening table, so music-taste matching cannot yet be computed at the user level from the available files alone.",
        "For that reason, the corrected prototype implements real second-degree filtering for age group and country, and reserves music-taste filtering as a documented next step if the listening-history file is added.",
    ]

    insights = [
        f"The cleaned graph contains {len(network):,} unique undirected friendships across {users['degree'].gt(0).sum():,} connected users.",
        f"The median user has {int(degree_summary['50%'])} direct friends but {int(second_summary['50%'])} second-degree candidates, which supports the core idea that the real opportunity lies beyond the first hop.",
        f"Second-degree opportunity scales quickly: the 90th percentile user reaches {int(second_summary['90%']):,} friends-of-friends after excluding direct ties.",
        f"Attribute overlap remains meaningful in the second-degree layer: the median user has {int(both_summary['50%'])} second-degree candidates sharing both country and age group.",
        f"The dataset is overwhelmingly UK-based, so country matching is informative for prototype logic but should not be over-interpreted as geographic diversity.",
    ]

    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset": {
            "title": "Last.fm UK User Graph Dataset",
            "source": "Zenodo",
            "doi": "10.5281/zenodo.10694369",
        },
        "caveats": caveats,
        "overview": {
            "users": int(len(users)),
            "connected_users": int(users["degree"].gt(0).sum()),
            "unique_friendships": int(len(network)),
            "median_degree": int(degree_summary["50%"]),
            "median_second_degree": int(second_summary["50%"]),
        },
        "quality_checks": {
            "missing_age": int(users["age_clean"].isna().sum()),
            "missing_country": int(users["country"].isna().sum()),
            "missing_gender": int(users["gender"].isna().sum()),
            "users_with_degree_zero": int(users["degree"].eq(0).sum()),
        },
        "summaries": {
            "degree": {key: round_float(value, 3) for key, value in degree_summary.to_dict().items()},
            "second_degree": {key: round_float(value, 3) for key, value in second_summary.to_dict().items()},
            "shared_both_second_degree": {key: round_float(value, 3) for key, value in both_summary.to_dict().items()},
            "friend_country_match_rate_mean": round_float(friend_match["friend_country_match_rate"].dropna().mean(), 4),
            "friend_age_group_match_rate_mean": round_float(friend_match["friend_age_group_match_rate"].dropna().mean(), 4),
        },
        "insights": insights,
        "charts": {
            "age_group_distribution": series_to_pairs(age_group_distribution),
            "top_countries": series_to_pairs(country_counts),
            "degree_histogram": histogram_from_bins(users["degree"], [0, 1, 3, 5, 10, 20, 50, 100, 250, 500]),
            "second_degree_histogram": histogram_from_bins(
                second_degree_summary["second_degree_count"],
                [0, 10, 25, 50, 100, 200, 500, 1000, 2000],
            ),
            "shared_attribute_histogram": histogram_from_bins(
                second_degree_summary["shared_both_second_degree"],
                [0, 1, 3, 5, 10, 20, 50, 100, 200],
            ),
            "prototype_case_sizes": [
                {
                    "label": f"User {case['user']['id']}",
                    "direct_friends": int(case["summary"]["direct_friends"]),
                    "second_degree_count": int(case["summary"]["second_degree_count"]),
                    "same_country_second_degree": int(case["summary"]["same_country_second_degree"]),
                    "same_age_group_second_degree": int(case["summary"]["same_age_group_second_degree"]),
                    "same_both_second_degree": int(case["summary"]["same_both_second_degree"]),
                    "visible_second_degree_count": int(case["summary"]["visible_second_degree_count"]),
                }
                for case in exemplar_cases
            ],
        },
        "prototype": {
            "supported_filters": [
                {"id": "same_country", "label": "Same country"},
                {"id": "same_age_group", "label": "Same age group"},
                {"id": "same_gender", "label": "Same gender"},
            ],
            "planned_filters": [
                {
                    "id": "same_music_taste",
                    "label": "Same music taste",
                    "status": "planned",
                    "note": "Needs a user-to-artist listening table, which is not present in the local workspace.",
                }
            ],
            "cases": exemplar_cases,
        },
    }

    return payload


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    serialized = json.dumps(payload, indent=2)
    OUTPUT_PATH.write_text(serialized, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
