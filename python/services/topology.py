"""Helpers for building a time-varying topology from satellite positions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean
from typing import Any, Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class TopologyConfig:
    """Parameters that control the synthesized inter-satellite topology."""

    neighbor_count: int = 2
    min_validity_seconds: float = 5.0
    default_validity_seconds: float = 20.0
    max_validity_seconds: float = 60.0
    speed_of_light_km_per_ms: float = 299.792458


class TopologyBuilder:
    """Builds a deterministic, time-varying graph from satellite positions."""

    def __init__(self, config: TopologyConfig | None = None) -> None:
        self.config = config or TopologyConfig()

    def normalize_satellites(self, satellites: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure satellites use the canonical coordinate and id fields."""
        normalized: List[Dict[str, Any]] = []
        for satellite in satellites:
            if satellite is None:
                continue
            normalized.append(
                {
                    "id": str(satellite.get("id", "")),
                    "x_km": float(satellite.get("x_km", 0.0)),
                    "y_km": float(satellite.get("y_km", 0.0)),
                    "z_km": float(satellite.get("z_km", 0.0)),
                }
            )
        return normalized

    def normalize_links(self, links: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure links use canonical routing keys."""
        normalized: List[Dict[str, Any]] = []
        for link in links:
            if link is None:
                continue
            normalized.append(
                {
                    "source": str(link.get("source", "")),
                    "target": str(link.get("target", "")),
                    "delay_ms": float(link.get("delay_ms", 0.0)),
                    "distance_km": float(link.get("distance_km", 0.0)),
                    "capacity_mbps": float(link.get("capacity_mbps", 0.0)),
                    "snr_db": float(link.get("snr_db", 0.0)),
                    "margin_db": float(link.get("margin_db", -100.0)),
                    "elevation_deg": float(link.get("elevation_deg", 0.0)),
                    "path_loss_db": float(link.get("path_loss_db", -100.0)),
                    "packet_loss_prob": float(link.get("packet_loss_prob", 0.0)),
                }
            )
        return normalized

    def build_links_from_positions(self, satellites: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Connect each satellite to its closest neighbours.

        This keeps the Python-side topology time-varying because the edge set
        changes whenever the satellite positions change.
        """
        nodes = self.normalize_satellites(satellites)
        if len(nodes) < 2:
            return []

        positions: List[Tuple[str, float, float, float]] = [
            (satellite["id"], satellite["x_km"], satellite["y_km"], satellite["z_km"])
            for satellite in nodes
        ]

        edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for index, (sat_id, x1, y1, z1) in enumerate(positions):
            distances: List[Tuple[float, int, str]] = []
            for other_index, (other_id, x2, y2, z2) in enumerate(positions):
                if other_index == index:
                    continue
                distance_km = math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)
                distances.append((distance_km, other_index, other_id))

            distances.sort(key=lambda item: (item[0], item[2]))
            for distance_km, _, other_id in distances[: self.config.neighbor_count]:
                source, target = sorted((sat_id, other_id))
                if source == target:
                    continue

                delay_ms = distance_km / self.config.speed_of_light_km_per_ms
                # Keep longer links feasible, but prefer shorter ones in Dijkstra.
                capacity_mbps = max(25.0, 1200.0 - (distance_km * 0.08))
                edges[(source, target)] = {
                    "source": source,
                    "target": target,
                    "delay_ms": round(delay_ms, 6),
                    "distance_km": round(distance_km, 6),
                    "capacity_mbps": round(capacity_mbps, 3),
                }

        return list(edges.values())

    def estimate_link_validity_seconds(
        self,
        satellites: Iterable[Dict[str, Any]],
        links: Iterable[Dict[str, Any]],
    ) -> float:
        """Return a conservative validity window for the current topology."""
        normalized_links = self.normalize_links(links)
        if not normalized_links:
            return self.config.default_validity_seconds

        distances = [link["distance_km"] for link in normalized_links if link["distance_km"] > 0.0]
        if not distances:
            return self.config.default_validity_seconds

        average_distance = mean(distances)
        validity = self.config.default_validity_seconds + ((4000.0 - average_distance) / 400.0)
        return float(
            min(
                self.config.max_validity_seconds,
                max(self.config.min_validity_seconds, validity),
            )
        )
