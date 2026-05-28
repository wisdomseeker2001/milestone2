from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from music_data_utils import compute_degree_table, load_network, load_users


AGE_GROUP_BINS = [9, 19, 29, 39, 49, 59, 120]
AGE_GROUP_LABELS = ["10s", "20s", "30s", "40s", "50s", "60+"]


@dataclass(frozen=True)
class SecondDegreeAssets:
    users: pd.DataFrame
    network: pd.DataFrame
    degree_table: pd.DataFrame
    adjacency: dict[int, set[int]]


def add_user_matching_features(users: pd.DataFrame) -> pd.DataFrame:
    enriched = users.copy()
    age_groups = pd.cut(enriched["age_clean"], bins=AGE_GROUP_BINS, labels=AGE_GROUP_LABELS)
    enriched["age_group"] = pd.Series(age_groups, index=enriched.index, dtype="string")
    return enriched


def load_second_degree_assets(root: str | Path = ".") -> SecondDegreeAssets:
    users = add_user_matching_features(load_users(root))
    network = load_network(root, deduplicate=True)
    degree_table = compute_degree_table(network)
    users = users.merge(degree_table, on="user_id", how="left")
    users["degree"] = users["degree"].fillna(0).astype("int64")
    adjacency = build_adjacency(network)
    return SecondDegreeAssets(users=users, network=network, degree_table=degree_table, adjacency=adjacency)


def build_adjacency(network: pd.DataFrame) -> dict[int, set[int]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for source, target in network[["user_id_source", "user_id_target"]].itertuples(index=False):
        source_id = int(source)
        target_id = int(target)
        adjacency[source_id].add(target_id)
        adjacency[target_id].add(source_id)
    return dict(adjacency)


def second_degree_candidates(user_id: int, adjacency: dict[int, set[int]]) -> set[int]:
    friends = adjacency.get(int(user_id), set())
    second_degree: set[int] = set()
    for friend_id in friends:
        second_degree.update(adjacency.get(int(friend_id), set()))
    second_degree.discard(int(user_id))
    second_degree.difference_update(friends)
    return second_degree


def mutual_friend_counts(
    user_id: int,
    candidates: set[int],
    adjacency: dict[int, set[int]],
) -> dict[int, int]:
    user_friends = adjacency.get(int(user_id), set())
    return {
        int(candidate): len(user_friends.intersection(adjacency.get(int(candidate), set())))
        for candidate in candidates
    }


def summarize_second_degree(users: pd.DataFrame, adjacency: dict[int, set[int]]) -> pd.DataFrame:
    meta = users.set_index("user_id")[["country", "age_group", "degree"]]
    rows: list[dict[str, object]] = []

    for user_id in meta.index.tolist():
        friends = adjacency.get(int(user_id), set())
        second_degree = second_degree_candidates(int(user_id), adjacency)
        mutual_counts = mutual_friend_counts(int(user_id), second_degree, adjacency)

        user_country = meta.at[int(user_id), "country"] if int(user_id) in meta.index else pd.NA
        user_age_group = meta.at[int(user_id), "age_group"] if int(user_id) in meta.index else pd.NA

        shared_country = 0
        shared_age_group = 0
        shared_both = 0
        for candidate in second_degree:
            candidate_country = meta.at[int(candidate), "country"] if int(candidate) in meta.index else pd.NA
            candidate_age_group = meta.at[int(candidate), "age_group"] if int(candidate) in meta.index else pd.NA

            country_match = pd.notna(user_country) and pd.notna(candidate_country) and candidate_country == user_country
            age_match = pd.notna(user_age_group) and pd.notna(candidate_age_group) and candidate_age_group == user_age_group

            shared_country += int(country_match)
            shared_age_group += int(age_match)
            shared_both += int(country_match and age_match)

        rows.append(
            {
                "user_id": int(user_id),
                "degree": int(len(friends)),
                "second_degree_count": int(len(second_degree)),
                "shared_country_second_degree": int(shared_country),
                "shared_age_group_second_degree": int(shared_age_group),
                "shared_both_second_degree": int(shared_both),
                "max_mutual_friends": int(max(mutual_counts.values(), default=0)),
                "avg_mutual_friends": round(sum(mutual_counts.values()) / len(mutual_counts), 3) if mutual_counts else 0.0,
            }
        )

    return pd.DataFrame(rows)


def friend_attribute_match_rates(users: pd.DataFrame, adjacency: dict[int, set[int]]) -> pd.DataFrame:
    meta = users.set_index("user_id")[["country", "age_group"]]
    rows: list[dict[str, object]] = []

    for user_id, friends in adjacency.items():
        if not friends:
            continue

        user_country = meta.at[int(user_id), "country"] if int(user_id) in meta.index else pd.NA
        user_age_group = meta.at[int(user_id), "age_group"] if int(user_id) in meta.index else pd.NA

        shared_country = 0
        shared_age = 0
        comparable_country = 0
        comparable_age = 0

        for friend_id in friends:
            friend_country = meta.at[int(friend_id), "country"] if int(friend_id) in meta.index else pd.NA
            friend_age_group = meta.at[int(friend_id), "age_group"] if int(friend_id) in meta.index else pd.NA

            if pd.notna(user_country) and pd.notna(friend_country):
                comparable_country += 1
                shared_country += int(friend_country == user_country)

            if pd.notna(user_age_group) and pd.notna(friend_age_group):
                comparable_age += 1
                shared_age += int(friend_age_group == user_age_group)

        rows.append(
            {
                "user_id": int(user_id),
                "friend_country_match_rate": (shared_country / comparable_country) if comparable_country else None,
                "friend_age_group_match_rate": (shared_age / comparable_age) if comparable_age else None,
            }
        )

    return pd.DataFrame(rows)


def build_ego_case(
    user_id: int,
    users: pd.DataFrame,
    adjacency: dict[int, set[int]],
    top_n_candidates: int = 18,
) -> dict[str, object]:
    meta = users.set_index("user_id")
    root_user = meta.loc[int(user_id)]
    direct_friends = adjacency.get(int(user_id), set())
    second_degree = second_degree_candidates(int(user_id), adjacency)
    mutual_counts = mutual_friend_counts(int(user_id), second_degree, adjacency)
    user_friends = adjacency.get(int(user_id), set())

    def country_match_with_root(candidate_id: int) -> bool:
        candidate_country = meta.loc[int(candidate_id), "country"]
        return (
            pd.notna(root_user["country"])
            and pd.notna(candidate_country)
            and candidate_country == root_user["country"]
        )

    def age_group_match_with_root(candidate_id: int) -> bool:
        candidate_age_group = meta.loc[int(candidate_id), "age_group"]
        return (
            pd.notna(root_user["age_group"])
            and pd.notna(candidate_age_group)
            and candidate_age_group == root_user["age_group"]
        )

    def user_payload(node_id: int, ring: str) -> dict[str, object]:
        row = meta.loc[int(node_id)]
        return {
            "id": int(node_id),
            "label": f"User {int(node_id)}",
            "ring": ring,
            "country": None if pd.isna(row["country"]) else str(row["country"]),
            "age_group": None if pd.isna(row["age_group"]) else str(row["age_group"]),
            "gender": None if pd.isna(row["gender"]) else str(row["gender"]),
            "degree": int(row["degree"]) if not pd.isna(row["degree"]) else len(adjacency.get(int(node_id), set())),
        }

    ranked_candidates = sorted(
        second_degree,
        key=lambda candidate_id: (
            mutual_counts.get(int(candidate_id), 0),
            int(country_match_with_root(int(candidate_id))) + int(age_group_match_with_root(int(candidate_id))),
            len(adjacency.get(int(candidate_id), set())),
        ),
        reverse=True,
    )[:top_n_candidates]

    nodes = [user_payload(int(user_id), "root")]
    nodes.extend(user_payload(friend_id, "first") for friend_id in sorted(direct_friends))
    nodes.extend(user_payload(candidate_id, "second") for candidate_id in ranked_candidates)

    visible_second_degree = set(ranked_candidates)
    edges: list[dict[str, int]] = []
    for friend_id in sorted(direct_friends):
        edges.append({"source": int(user_id), "target": int(friend_id)})
        for candidate_id in visible_second_degree.intersection(adjacency.get(int(friend_id), set())):
            edges.append({"source": int(friend_id), "target": int(candidate_id)})

    direct_friend_payload = []
    for friend_id in sorted(direct_friends):
        friend_row = meta.loc[int(friend_id)]
        bridge_candidates = sorted(visible_second_degree.intersection(adjacency.get(int(friend_id), set())))
        direct_friend_payload.append(
            {
                "id": int(friend_id),
                "label": f"User {int(friend_id)}",
                "country": None if pd.isna(friend_row["country"]) else str(friend_row["country"]),
                "age_group": None if pd.isna(friend_row["age_group"]) else str(friend_row["age_group"]),
                "gender": None if pd.isna(friend_row["gender"]) else str(friend_row["gender"]),
                "degree": int(friend_row["degree"]),
                "bridge_count": int(len(bridge_candidates)),
                "bridge_candidates": [int(candidate_id) for candidate_id in bridge_candidates],
            }
        )

    ego_gender = str(root_user["gender"]) if pd.notna(root_user["gender"]) else "unknown"

    candidates_payload = []
    for candidate_id in ranked_candidates:
        candidate_row = meta.loc[int(candidate_id)]
        same_country = country_match_with_root(int(candidate_id))
        same_age_group = age_group_match_with_root(int(candidate_id))
        cand_gender = str(candidate_row["gender"]) if pd.notna(candidate_row["gender"]) else "unknown"
        same_gender = ego_gender == cand_gender and ego_gender not in ("unknown", "not_shared")
        mutual_friend_ids = sorted(int(friend_id) for friend_id in user_friends.intersection(adjacency.get(int(candidate_id), set())))
        candidates_payload.append(
            {
                "id": int(candidate_id),
                "label": f"User {int(candidate_id)}",
                "country": None if pd.isna(candidate_row["country"]) else str(candidate_row["country"]),
                "age_group": None if pd.isna(candidate_row["age_group"]) else str(candidate_row["age_group"]),
                "gender": cand_gender,
                "degree": int(candidate_row["degree"]),
                "mutual_friends": int(mutual_counts.get(int(candidate_id), 0)),
                "mutual_friend_ids": mutual_friend_ids,
                "same_country": bool(same_country),
                "same_age_group": bool(same_age_group),
                "same_gender": bool(same_gender),
                "shared_attribute_count": int(same_country) + int(same_age_group) + int(same_gender),
                "score": int(mutual_counts.get(int(candidate_id), 0)) * 2 + int(same_country) + int(same_age_group) + int(same_gender),
            }
        )

    same_country_total = sum(country_match_with_root(int(candidate_id)) for candidate_id in second_degree)
    same_age_group_total = sum(age_group_match_with_root(int(candidate_id)) for candidate_id in second_degree)
    same_both_total = sum(
        country_match_with_root(int(candidate_id)) and age_group_match_with_root(int(candidate_id))
        for candidate_id in second_degree
    )

    return {
        "user": user_payload(int(user_id), "root"),
        "summary": {
            "direct_friends": len(direct_friends),
            "second_degree_count": len(second_degree),
            "visible_second_degree_count": len(ranked_candidates),
            "same_country_second_degree": int(same_country_total),
            "same_age_group_second_degree": int(same_age_group_total),
            "same_both_second_degree": int(same_both_total),
            "visible_same_country": int(sum(item["same_country"] for item in candidates_payload)),
            "visible_same_age_group": int(sum(item["same_age_group"] for item in candidates_payload)),
            "visible_same_both": int(sum(item["same_country"] and item["same_age_group"] for item in candidates_payload)),
        },
        "nodes": nodes,
        "edges": edges,
        "direct_friends": direct_friend_payload,
        "candidates": candidates_payload,
    }


def choose_exemplar_users(
    users: pd.DataFrame,
    adjacency: dict[int, set[int]],
    summary: pd.DataFrame | None = None,
    max_cases: int = 3,
) -> list[int]:
    if summary is None:
        summary = summarize_second_degree(users, adjacency)
    merged = summary.merge(
        users[["user_id", "country", "age_group"]],
        on="user_id",
        how="left",
    )
    filtered = merged.loc[
        merged["country"].notna()
        & merged["age_group"].notna()
        & merged["degree"].between(8, 24)
        & merged["second_degree_count"].between(40, 220)
    ].copy()

    filtered["match_score"] = (
        filtered["shared_country_second_degree"]
        + filtered["shared_age_group_second_degree"]
        + filtered["shared_both_second_degree"] * 2
    )

    picked: list[int] = []
    seen_age_groups: set[str] = set()

    for row in filtered.sort_values(
        ["match_score", "second_degree_count", "degree"],
        ascending=[False, False, False],
    ).itertuples(index=False):
        age_group = str(row.age_group)
        if age_group in seen_age_groups and len(picked) + 1 < max_cases:
            continue
        picked.append(int(row.user_id))
        seen_age_groups.add(age_group)
        if len(picked) >= max_cases:
            break

    if len(picked) < max_cases:
        for user_id in filtered["user_id"].tolist():
            if int(user_id) not in picked:
                picked.append(int(user_id))
            if len(picked) >= max_cases:
                break

    return picked
