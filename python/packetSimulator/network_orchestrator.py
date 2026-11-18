"""Orchestrator for the packet-level network simulator."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from threading import Lock
from collections import deque
from typing import Any, Dict, List, Optional, Tuple
from math import floor, isfinite
import numpy as np
import heapq

from services.topology import TopologyBuilder
from algorithms.dijkstra_router import CostMetric, DijkstraRouter, RouteResult
from packetSimulator.network_models import Packet, ClassConfig, ClassQueueState, NodeCounters
from packetSimulator.network_models import NetworkNode, SourceState, ReceiverState, BackgroundPacket, WindowUserMetrics, StepNodeMetrics

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Population centres used for realistic background-traffic injection.
# Format: (latitude_deg, longitude_deg, relative_weight)
# Weights are roughly proportional to metropolitan-area population (millions).
# ---------------------------------------------------------------------------
POPULATION_CENTERS = [
    # East Asia
    (35.7,  139.7,  37.4),   # Tokyo
    (31.2,  121.5,  26.3),   # Shanghai
    (23.1,  113.3,  25.0),   # Guangzhou / Pearl River Delta
    (39.9,  116.4,  21.5),   # Beijing
    (35.0,  135.8,  19.0),   # Osaka
    (37.6,  127.0,  25.5),   # Seoul
    (22.3,  114.2,   7.5),   # Hong Kong
    (25.1,  121.6,   7.0),   # Taipei
    # South / Southeast Asia
    (28.6,   77.2,  32.0),   # Delhi
    (19.1,   72.9,  21.0),   # Mumbai
    (22.6,   88.4,  14.9),   # Kolkata
    (13.1,   80.3,  11.0),   # Chennai
    (-6.2,  106.8,  10.6),   # Jakarta
    (14.6,  121.0,  13.9),   # Manila
    (13.8,  100.5,  10.5),   # Bangkok
    (10.8,  106.7,   9.3),   # Ho Chi Minh City
    (33.7,   73.1,  11.0),   # Islamabad / Rawalpindi
    (24.9,   67.0,  16.1),   # Karachi
    # Middle East / Central Asia
    (30.0,   31.2,  20.9),   # Cairo
    (41.0,   29.0,  15.5),   # Istanbul
    (33.3,   44.4,   8.8),   # Baghdad
    (24.7,   46.7,   7.6),   # Riyadh
    # Sub-Saharan Africa
    ( 6.5,    3.4,  14.8),   # Lagos
    (-26.2,   28.0,  5.8),   # Johannesburg
    ( 0.3,   32.6,   3.6),   # Kampala
    (-4.3,   15.3,  14.3),   # Kinshasa
    # Europe
    (55.8,   37.6,  12.5),   # Moscow
    (51.5,   -0.1,  14.0),   # London
    (48.9,    2.3,  12.2),   # Paris
    (52.5,   13.4,   3.8),   # Berlin
    (41.4,    2.2,   5.6),   # Barcelona / Madrid area
    (45.5,    9.2,   3.3),   # Milan
    (45.1,    7.7,   2.3),   # Turin  (source UE)
    (48.2,   16.4,   1.9),   # Vienna
    # Americas
    (40.7,  -74.0,  20.0),   # New York  (destination UE)
    (34.1, -118.2,  13.2),   # Los Angeles
    (41.8,  -87.6,   9.6),   # Chicago
    (-23.5,  -46.6,  22.4),  # São Paulo
    (-34.6,  -58.4,  15.2),  # Buenos Aires
    (19.4,  -99.1,  21.6),   # Mexico City
    (4.7,   -74.1,   8.0),   # Bogotá
    (-12.0,  -77.0,  10.8),  # Lima
    # Oceania
    (-33.9,  151.2,   5.3),  # Sydney
    (-37.8,  145.0,   5.0),  # Melbourne
]


class NetworkOrchestrator:
    """Implements packet-level network simulator."""

    def __init__(self, state) -> None:
        self._router = DijkstraRouter()
        self.topology = TopologyBuilder()
        self._state = state
        self._init = False
        self._Nwin: float = 5.0
        self._path_links: List[Dict[str, Any]] = []
        self._active_route_signature: Tuple = ()
        self._nodes: List[NetworkNode] = []
        self._class_configs: List[ClassConfig] = []
        self._time_step: float = 0.0
        self._source = None
        self._receiver = None
        self._rng = np.random.default_rng(seed=42)
        self._node_neighbours: Dict[str, float] = {}

        self._future_events: List[Tuple[float, int, Dict[str, Any]]] = []
        self._event_seq: int = 0
        self._current_step_start: float = 0.0
        self._current_step_end: float = 0.0
        self._window_stats = []
        self._step_node_metrics = {}
        self._step_drop_count = 0
        self._step_created_count = 0
        self._step_arrived_count = 0
        self._source_node = None
        self._nodes_by_id: Dict[str, NetworkNode] = {}

        
    def compute_route(self) -> RouteResult:
        """Compute route and aggregate path statistics."""
        if self._state.metric == CostMetric.TOTAL_DELAY_MS:
            if self._state.background_traffic_injection_type == "population_based":
                # Use the population-weighted method so the live slider
                # update is consistent with what run_simulation() applies.
                nodes = self._compute_population_weighted_load()
            else:
                # background traffic is offered only in the satellite connected to UE1 (source of our flow of interest)
                nodes = self._compute_background_traffic_load()
            route = self._router.compute_route(
                links=self._state.last_links,
                source=self._state.source,
                destination=self._state.destination,
                metric=self._state.metric,
                nodes=nodes,
                packet_size_bytes=self._state.packet_size_bytes,
                buffer_size=self._state.buffer_size,
                )
        else:
            route = self._router.compute_route(
                links=self._state.last_links,
                source=self._state.source,
                destination=self._state.destination,
                metric=self._state.metric,
                )
        return route

    def extract_route_links(self) -> None:
        """
        Build the ordered path-local link list for the current route.

        The route path is the source of truth for hop order and direction.
        The raw link list may store a matching physical link in either direction.
        Returned path_links are always normalized so that:
            path_links[i]["source"] == route.path[i]
            path_links[i]["target"] == route.path[i + 1]
        """
        route = self._state.last_route
        links = self._state.last_links or []

        if route is None:
            logger.warning("Routing suspended: No route available in simulation state.")
            self._path_links = []
            return

        if not getattr(route, "success", False):
            logger.warning("Network Outage: Route computation currently unviable.")
            self._path_links = []
            return

        path = list(getattr(route, "path", []) or [])
        if len(path) < 2:
            logger.warning("Network Outage: Terminal currently out of satellite footprint masks.")
            self._path_links = []
            return

        link_index: Dict[tuple[str, str], Dict[str, Any]] = {}
        for link in links:
            src = str(link.get("source"))
            dst = str(link.get("target"))
            link_index[(src, dst)] = link

        path_links: List[Dict[str, Any]] = []

        for hop_index in range(len(path) - 1):
            u = str(path[hop_index])
            v = str(path[hop_index + 1])

            raw_link = link_index.get((u, v))
            if raw_link is None:
                raw_link = link_index.get((v, u))

            if raw_link is None:
                logger.error('Error in NetworkOrchestrator extract_route_links(): Missing physical link for route hop %r -> %r', u, v)
                continue

            normalized_link = dict()
            normalized_link["source"] = u
            normalized_link["target"] = v
            normalized_link["hop_index"] = hop_index
            normalized_link["capacity_mbps"] = raw_link["capacity_mbps"]
            normalized_link["delay_ms"] = raw_link["delay_ms"]
            path_links.append(normalized_link)
        
        self._path_links = path_links


    def refresh_active_route_link_properties(self) -> None:
        """Refresh link capacities and delays without rebuilding simulator state."""
        route = self._state.last_route
        if (
            not self._init
            or route is None
            or not getattr(route, "success", False)
            or tuple(route.path) != self._active_route_signature
        ):
            return

        self.extract_route_links()
        if len(self._nodes) != len(self._path_links):
            raise RuntimeError(
                "Cannot refresh active route links: simulator nodes and route links differ in length"
            )

        for node, link in zip(self._nodes, self._path_links):
            if str(node.node_id) != link["source"]:
                raise RuntimeError(
                    "Cannot refresh active route links: simulator node order does not match route"
                )
            node.link_capacity_mbps = float(link["capacity_mbps"])
            node.link_delay_ms = float(link["delay_ms"])

    
    def _build_network_entities(self) -> None:
        """
        Build the path-local packet simulator entities:
        - one source
        - one receiver
        - one forwarding node per route satellite/output hop
        """
        route = self._state.last_route
        path_links = list(self._path_links or [])
        priority_levels = list(self._state.priority_levels or [])

        if route is None or not getattr(route, "success", False) or len(path_links) == 0:
            logger.info("Simulation state updated: Operating in DISCONNECTED mode. Preserving network entities.")
            return

        path = list(getattr(route, "path", []) or [])

        if len(path) < 2:
            raise ValueError(f"Cannot build network entities: invalid route path {path!r}")

        if len(path_links) != len(path) - 1:
            raise ValueError(
                f"Cannot build network entities: expected {len(path) - 1} path links, got {len(path_links)}"
            )

        if not priority_levels:
            raise ValueError("Cannot build network entities: priority_levels is empty.")
        
        old_source = self._source

        class_configs: List[ClassConfig] = []
        for item in priority_levels:
            cfg = ClassConfig(
                priority=int(item.get("priority", 0)),
                scheduling=str(item.get("scheduling", "WRR")).strip().upper(),
                weight=max(1, int(item.get("weight", 1))),
                label=str(item.get("label", "")),
                fraction_of_load=float(item.get("fraction_of_load", 0.0)),
            )
            class_configs.append(cfg)

        class_configs.sort(key=lambda x: x.priority)

        user_priority = int(self._state.user_flow_priority)
        if user_priority not in {cfg.priority for cfg in class_configs}:
            raise ValueError(
                f"user_flow_priority={user_priority} is not defined in priority_levels"
            )

        self._source = SourceState(
            node_id=str(path[0]),
            flow_id=f"{path[0]}->{path[-1]}",
            user_priority=user_priority,
            packet_size_bytes=int(self._state.packet_size_bytes),
            traffic_mode=str(self._state.user_flow_cbr_vbr).strip().upper(),
            peak_rate_mbps=float(self._state.user_flow_peak_rate),
            avg_rate_mbps=float(self._state.user_flow_avg_rate),
            burstiness=float(self._state.user_flow_burstiness),
        )

        if old_source is not None:
            self._source.next_packet_id = int(old_source.next_packet_id)
            self._source.packets_created = int(old_source.packets_created)
            self._source.next_generation_time = float(old_source.next_generation_time)
            self._source.vbr_is_on = bool(old_source.vbr_is_on)
            self._source.next_state_change_time = float(old_source.next_state_change_time)

        self._receiver = ReceiverState(
            node_id=str(path[-1]),
        )

        self._nodes = []
        for node_index in range(0, len(path) - 1):
            outgoing_link = path_links[node_index]
            is_source_node = (node_index == 0)
            is_first_satellite = (node_index == 1)

            node = NetworkNode(
                node_id=str(path[node_index]),
                hop_index=node_index,
                buffer_size_packets=int(self._state.buffer_size),
                link_capacity_mbps=float(outgoing_link["capacity_mbps"]),
                link_delay_ms=float(outgoing_link["delay_ms"]),
                scheduler_kind="HYBRID",
                node_role="source" if is_source_node else "forwarder",
                #include_in_route_stats=not is_source_node,
                include_in_route_stats=True,
                is_access_node=is_first_satellite,
            )

            for cfg in class_configs:
                node.class_states[cfg.priority] = ClassQueueState(config=cfg)
                if cfg.scheduling == "WRR":
                    node.wrr_deficit[cfg.priority] = 0

            self._nodes.append(node)

        self._class_configs = class_configs
        self._source_node = self._nodes[0]
        self._nodes_by_id = {str(node.node_id): node for node in self._nodes}

        self._state.packets_created = 0
        self._state.packets_arrived = 0
        self._state.user_measured_throughput = {}
        self._state.user_measured_latency = {}
        self._state.user_measured_loss_ratio = 0.0
        self._state.route_node_stats = []

    # ------------------------------------------------------------------
    # Background-traffic helpers
    # ------------------------------------------------------------------
    def _compute_satellites_over_cities(self,satellites,total_rate):
        """ Find out which satellites are above big population centers,
        and estimate their injected load."""
        # ── Build satellite ECEF position map ─────────────────────────
        sat_pos: Dict[str, Tuple[float, float, float]] = {}
        for s in satellites:
            try:
                sid = str(s["id"])
                sat_pos[sid] = (
                    float(s["x_km"]),
                    float(s["y_km"]),
                    float(s["z_km"]),
                )
            except Exception:
                continue

        R_EARTH = 6378.0
        ELEVATION_MIN_RAD = math.radians(10.0)
        total_weight = sum(w for _, _, w in POPULATION_CENTERS)
        if total_weight <= 0.0:
            return

        # ── Per-satellite injected load from population centres ───────
        node_injected: Dict[str, float] = {sid: 0.0 for sid in sat_pos}

        for lat, lon, weight in POPULATION_CENTERS:
            lat_r = math.radians(lat)
            lon_r = math.radians(lon)
            gx = R_EARTH * math.cos(lat_r) * math.cos(lon_r)
            gy = R_EARTH * math.cos(lat_r) * math.sin(lon_r)
            gz = R_EARTH * math.sin(lat_r)
            g_mag = math.sqrt(gx**2 + gy**2 + gz**2)

            traffic_for_center = total_rate * (weight / total_weight)

            # Find all satellites visible from this ground point
            visible: List[Tuple[float, str]] = []
            for sid, (sx, sy, sz) in sat_pos.items():
                dx = sx - gx
                dy = sy - gy
                dz = sz - gz
                dist = math.sqrt(dx**2 + dy**2 + dz**2)
                if dist < 1e-3:
                    continue
                # Elevation angle: angle between slant vector and local horizon
                dot = gx * dx + gy * dy + gz * dz
                cos_zenith = dot / (g_mag * dist)
                elevation = math.asin(max(-1.0, min(1.0, cos_zenith)))
                if elevation >= ELEVATION_MIN_RAD:
                    visible.append((elevation, sid))

            if not visible:
                continue

            # Inject into the highest-elevation (best-link) visible satellite
            visible.sort(reverse=True)
            best_sid = visible[0][1]
            node_injected[best_sid] = node_injected.get(best_sid, 0.0) + traffic_for_center
        return node_injected

    def _compute_background_traffic_load(self) -> None:
        """
        Inject background load from the source-side access
        satellite only, propagating outward via BFS with equal splitting.

        Also computes, for every node on the path, the fraction of its served
        background traffic that must be forwarded to the next path node
        (node.forward_fraction), based on the number of ISLs (M) reported in
        self._node_neighbours: forward_fraction = 1/(M-1), excluding the link the
        traffic just arrived from
        """
        initial_rate = float(getattr(self._state, "background_traffic_mbps", 0.0))
        links = self._state.last_links or []
        satellites = (self._state.last_positions or {}).get("satellites", [])
        
        if not satellites:
            logger.error(
                "_compute_background_traffic_load: no satellite positions available, exiting."
            )
            return
        
        # Find out which satellites are over large population centers and the amount of traffic injected into them
        node_injected = self._compute_satellites_over_cities(satellites, initial_rate)

        adjacency = {}
        neighbor_count = {}
        for link in links:
            src = str(link["source"])
            dst = str(link["target"])
            if src not in adjacency:
                adjacency[src] = []
                neighbor_count[src] = 0
            adjacency[src].append(dst)
            neighbor_count[src] += 1
            if dst not in adjacency:
                adjacency[dst] = []
                neighbor_count[dst] = 0
            adjacency[dst].append(src)
            neighbor_count[dst] += 1
        self._node_neighbours = neighbor_count

        node_loads = {node: 0.0 for node in adjacency}

        access_satellite_id = None
        for node in self._nodes:
            if getattr(node, "is_access_node", False) or node.hop_index == 1:
                access_satellite_id = str(node.node_id)
                break

        if access_satellite_id and access_satellite_id in node_loads:
            node_loads[access_satellite_id] = initial_rate
            queue = deque([access_satellite_id])
        else:
            queue = deque()

        processed = set()

        while queue:
            current = queue.popleft()
            if current in processed:
                continue

            processed.add(current)
            current_load = node_loads.get(current, 0.0)
            neighbors = adjacency.get(current, [])

            if not neighbors:
                continue

            split_rate = current_load / len(neighbors)

            for neighbor in neighbors:
                if neighbor in node_loads:
                    node_loads[neighbor] += split_rate
                else:
                    node_loads[neighbor] = split_rate

                if neighbor not in processed and neighbor not in queue:
                    queue.append(neighbor)

        node_loads_per_link = dict()
        for node_id, node_load in node_loads.items():
            n_neighbors = neighbor_count.get(node_id, 0)
            node_loads_per_link[node_id] = (node_load / n_neighbors) if n_neighbors > 0 else 0.0

        last_hop_index = len(self._nodes) - 1

        for node in self._nodes:
            current_id = str(node.node_id)
            total_node_accumulated_load = node_loads.get(current_id, 0.0)

            # Count ISLs robustly by checking both directions in the raw links array
            connected_links = [
                lk for lk in links
                if str(lk.get("source")) == current_id or str(lk.get("target")) == current_id
            ]
            num_isls = len(connected_links)

            # If it's a satellite (more than 2 links typically, or just not the ground station)
            if num_isls > 1 and node.node_role != "source":
                node.background_load_mbps = total_node_accumulated_load / float(num_isls)
            else:
                node.background_load_mbps = total_node_accumulated_load

            # remove background traffic in ground-satellite service links if the satellite is not over a big city
            if node.node_role == "source" and node_injected[self._nodes[1].node_id] == 0:
                # The source-side satellite is covering a remote area. There is no background  in the ground-satellite link
                node.background_load_mbps = 0
            if node.hop_index >= last_hop_index and node_injected[current_id] == 0:
                # The destination-side satellite is covering a remote area.
                node.background_load_mbps = 0

            next_hop_index = int(node.hop_index) + 1
            if next_hop_index >= last_hop_index:
                # The next node is the destination. No background packet should arrive at RX
                # Still, they are transmitted (if the destination is in a big city)
                node.forward_fraction = 0.0
            else:
                next_node_id = self._nodes[next_hop_index].node_id
                connected_links_next_node = [
                    lk for lk in links
                    if str(lk.get("source")) == next_node_id or str(lk.get("target")) == next_node_id
                ]
                num_isls_next_node = len(connected_links_next_node)
                if node.hop_index == last_hop_index - 1 and node_injected[next_node_id] == 0:
                    # if this is the penultimate node and the next node is not on a large city,
                    # no background traffic needs to arrive to that node
                    node.forward_fraction = 0.0
                else:
                    node.forward_fraction = 1.0 / (num_isls_next_node - 1) if num_isls_next_node > 1 else 0.0

        return node_loads_per_link  # it's all the loads of all satellites. Used to estimate queueing delay to run Dijkstra with total_delay_ms

    def _compute_population_weighted_load(self) -> None:
        """
        Realistic background-traffic injection based on global population centres.

        For each population centre in POPULATION_CENTERS the nearest visible
        satellite (elevation >= 10 degrees) receives a share of the total background
        load proportional to that centre's weight. The load is then propagated
        outward via BFS with equal ISL splitting, mirroring the physical
        assumption that every satellite forwards traffic to all its neighbours
        at equal rates.

        Also computes node.forward_fraction for every path node (see
        _compute_background_traffic_load for the exact semantics).

        Falls back to _compute_background_traffic_load() when satellite
        position data is not yet available.
        """
        total_rate = float(getattr(self._state, "background_traffic_mbps", 0.0))
        links = self._state.last_links or []
        satellites = (self._state.last_positions or {}).get("satellites", [])

        # ── Guard: fall back to simpler method if no satellite data ───
        if not satellites:
            logger.warning(
                "_compute_population_weighted_load: no satellite positions "
                "available, falling back to simple injection."
            )
            self._compute_background_traffic_load()
            return

        if total_rate <= 0.0:
            for node in self._nodes:
                node.background_load_mbps = 0.0
                node.forward_fraction = 0.0
            return

        # Find out which satellites are over large population centers and the amount of traffic injected into them
        node_injected = self._compute_satellites_over_cities(satellites, total_rate)
        
        # ── Build bidirectional adjacency from ISL link list ──────────
        adjacency: Dict[str, List[str]] = {}
        neighbor_count: Dict[str, float] = {}
        for link in links:
            src = str(link["source"])
            dst = str(link["target"])
            adjacency.setdefault(src, []).append(dst)
            adjacency.setdefault(dst, []).append(src)
            if src not in neighbor_count:
                neighbor_count[src] = 0
            neighbor_count[src] += 1
            if dst not in neighbor_count:
                neighbor_count[dst] = 0
            neighbor_count[dst] += 1
        self._node_neighbours = neighbor_count

        # ── BFS propagation from each satellite's injected load ───────
        final_loads: Dict[str, float] = {sid['id']: 0.0 for sid in satellites}

        for start_node, injected in node_injected.items():
            if injected <= 0.0 or start_node not in adjacency:
                continue
            local: Dict[str, float] = {start_node: injected}
            queue: deque = deque([start_node])
            processed: set = set()

            while queue:
                cur = queue.popleft()
                if cur in processed:
                    continue
                processed.add(cur)
                cur_load = local.get(cur, 0.0)
                neighbors = [
                    n for n in adjacency.get(cur, []) if n not in processed
                ]
                if not neighbors:
                    continue
                split = cur_load / len(neighbors)
                for n in neighbors:
                    local[n] = local.get(n, 0.0) + split
                    if n not in processed:
                        queue.append(n)

            for nid, load in local.items():
                final_loads[nid] = final_loads.get(nid, 0.0) + load

        node_loads_per_link = dict()
        for node_id, final_load in final_loads.items():
            n_neighbors = neighbor_count.get(node_id, 0)
            node_loads_per_link[node_id] = (final_load / n_neighbors) if n_neighbors > 0 else 0.0

        last_hop_index = len(self._nodes) - 1

        # ── Apply accumulated load to active simulation nodes ─────────
        for node in self._nodes:
            nid = str(node.node_id)
            connected = [
                lk for lk in links
                if str(lk.get("source")) == nid or str(lk.get("target")) == nid
            ]
            num_isls = len(connected)
            total = final_loads.get(nid, 0.0)
            if num_isls > 1 and node.node_role != "source":
                node.background_load_mbps = total / float(num_isls)
            else:
                node.background_load_mbps = total
            
            # remove background traffic in ground-satellite service links if the satellite is not over a big city
            if node.node_role == "source" and node_injected[self._nodes[1].node_id] == 0:
                # The source-side satellite is covering a remote area. There is no background  in the ground-satellite link
                node.background_load_mbps = 0
            if node.hop_index >= last_hop_index and node_injected[nid] == 0:
                # The destination-side satellite is covering a remote area.
                node.background_load_mbps = 0

            next_hop_index = int(node.hop_index) + 1
            if next_hop_index >= last_hop_index:
                # The next node is the destination. No background packet should arrive at RX
                # Still, they are transmitted (if the destination is in a big city)
                node.forward_fraction = 0.0
            else:
                next_node_id = self._nodes[next_hop_index].node_id
                connected_links_next_node = [
                    lk for lk in links
                    if str(lk.get("source")) == next_node_id or str(lk.get("target")) == next_node_id
                ]
                num_isls_next_node = len(connected_links_next_node)
                if node.hop_index == last_hop_index - 1 and node_injected[next_node_id] == 0:
                    # if this is the penultimate node and the next node is not on a large city,
                    # no background traffic needs to arrive to that node
                    node.forward_fraction = 0.0
                else:
                    node.forward_fraction = 1.0 / (num_isls_next_node - 1) if num_isls_next_node > 1 else 0.0

        return node_loads_per_link  # it's all the loads of all satellites. Used to estimate queueing delay to run Dijkstra with total_delay_ms

    def _recover_after_handover(self):
        # After an handover, we must initialize again some things in our simulation
        self._future_events = []
        self.extract_route_links()
        self._build_network_entities()
            

    def _initialize(self):
        self.extract_route_links()
        self._build_network_entities()

        if self._source is None:
            self._init = True
            return
        
        if self._source.next_generation_time <= 0.0:
            self._source.next_generation_time = 0.0
        self._source.vbr_is_on = bool(self._source.vbr_is_on)
        if self._source.next_state_change_time < 0.0:
            self._source.next_state_change_time = 0.0

        self._future_events = []
        self._event_seq = 0
        self._init = True


    def run_simulation(self, handover_detected: bool = False):
        """Entry point to run the network-layer packet-based simulation"""
        route_signature = tuple(self._state.last_route.path) if self._state.last_route and self._state.last_route.success else ()

        if not self._init:
            self._initialize()
            self._active_route_signature = route_signature
        elif handover_detected or route_signature != self._active_route_signature:
            # The route has changed. Must set up the simulator again
            self._recover_after_handover()
            self._active_route_signature = route_signature

        t0 = float(self._state.ref_time_previous)
        t1 = float(self._state.ref_time_current)

        if t1 <= t0:
            logger.error("Error in NetworkOrchestrator: invalid step interval")
            self._state.packets_created = 0
            self._state.packets_arrived = 0
            self._state.user_measured_throughput = {}
            self._state.user_measured_latency = {}
            self._state.user_measured_loss_ratio = 0.0
            self._state.route_node_stats = []
            return

        self._current_step_start = t0
        self._current_step_end = t1
        self._Nwin = max(1, floor((t1 - t0) / float(self._state.monitor_interval)))
        self._time_step = t1 - t0

        self._reset_step_accumulators(t0, t1)

        # Compute the amount of background traffic (diffusion model: replaces rho_congestion)
        if self._state.background_traffic_injection_type == "population_based":
            # Use the population-weighted method so the live slider
            # update is consistent with what run_simulation() applies.
            self._compute_population_weighted_load()
        else:
            # background traffic is offered only in the satellite connected to UE1 (source of our flow of interest)
            self._compute_background_traffic_load()

        # Update the background traffic rates of the nodes in the path, now that
        # background_load_mbps and forward_fraction have been (re)computed above.
        self._initialize_background_arrival_rates()

        event_heap: List[Tuple[float, int, Dict[str, Any]]] = []
        deferred_events: List[Tuple[float, int, Dict[str, Any]]] = []

        def push_event(ev_time: float, ev: Dict[str, Any]) -> None:
            self._event_seq += 1
            item = (float(ev_time), self._event_seq, ev)
            if ev_time < t1:
                heapq.heappush(event_heap, item)
            else:
                deferred_events.append(item)

        # put inside FES the events left from the previous call of run_simulation(),
        # for instance packets that were created just before the end of the previous step (e.g. at t1-0.0001s) and
        # are going to be received in this new window
        for ev_time, seq, ev in self._future_events:
            if ev_time < t1:
                heapq.heappush(event_heap, (ev_time, seq, ev))
            else:
                deferred_events.append((ev_time, seq, ev))
        self._future_events = []

        if self._source_node is not None:
            user_packets = self._generate_user_packets(t0, t1)
            for pkt in user_packets:
                # Put inside FES the packets that our source has to create in this time window
                push_event(
                    pkt.created_at,
                    {
                        "event_type": "user_generate",
                        "packet": pkt,
                        "node_id": self._source_node.node_id,
                    },
                )

        bg_by_node = self._generate_all_background_traffic(t0, t1, self._rng)
        for node_id, packets in bg_by_node.items():
            for pkt in packets:
                # Put inside FES all the background packet arrival events in the nodes of the network
                push_event(
                    pkt.arrival_time,
                    {
                        "event_type": "bg_arrival",
                        "packet": pkt,
                        "node_id": node_id,
                    },
                )

        # ── EVENT LOOP ────────────────────────────────────────────────
        while event_heap:
            now, _, ev = heapq.heappop(event_heap)
            if now >= t1:
                deferred_events.append((now, self._event_seq, ev))
                continue

            event_type = ev["event_type"]

            if event_type == "user_generate":
                node = self._get_node_by_id(ev["node_id"])
                if node is None:
                    continue
                packet = ev["packet"]
                admitted = self._enqueue_user_packet(node, packet, now)
                self._register_user_packet_seen_at_node(node, admitted)
                if not admitted:
                    self._register_dropped_user_packet(node, packet, now)
                else:
                    tx_ev = self._start_transmission(node, now)
                    if tx_ev is not None:
                        if tx_ev.get("is_user_packet", False):
                            self._register_user_packet_transmitted_from_node(
                                node, float(tx_ev["queue_delay_ms"])
                            )
                        push_event(tx_ev["tx_end_time"], tx_ev)

            elif event_type == "bg_arrival":
                node = self._get_node_by_id(ev["node_id"])
                packet = ev["packet"]
                admitted = self._enqueue_background_packet(node, packet, now)
                if admitted:
                    tx_ev = self._start_transmission(node, now)
                    if tx_ev is not None:
                        if tx_ev.get("is_user_packet", False):
                            self._register_user_packet_transmitted_from_node(
                                node, float(tx_ev["queue_delay_ms"])
                            )
                        push_event(tx_ev["tx_end_time"], tx_ev)

            elif event_type == "node_arrival":
                node = self._get_node_by_id(ev["next_node_id"])
                packet = ev["packet"]
                admitted = self._enqueue_user_packet(node, packet, now)
                self._register_user_packet_seen_at_node(node, admitted)
                if not admitted:
                    self._register_dropped_user_packet(node, packet, now)
                else:
                    tx_ev = self._start_transmission(node, now)
                    if tx_ev is not None:
                        if tx_ev.get("is_user_packet", False):
                            self._register_user_packet_transmitted_from_node(
                                node, float(tx_ev["queue_delay_ms"])
                            )
                        push_event(tx_ev["tx_end_time"], tx_ev)

            elif event_type == "receiver_arrival":
                packet = ev["packet"]
                self._register_arrived_user_packet(packet, now)

            elif event_type == "tx_complete":
                node = self._get_node_by_id(ev["node_id"])
                packet = ev["packet"]
                next_ev = self._complete_transmission(node, packet, now)
                if next_ev is not None:
                    arrival_time = float(next_ev["arrival_time"])
                    push_event(arrival_time, next_ev)

                next_tx_ev = self._start_transmission(node, now)
                if next_tx_ev is not None:
                    if next_tx_ev.get("is_user_packet", False):
                        self._register_user_packet_transmitted_from_node(
                            node, float(next_tx_ev["queue_delay_ms"])
                        )
                    push_event(next_tx_ev["tx_end_time"], next_tx_ev)

            else:
                raise ValueError(f"Unsupported event type: {event_type!r}")

        self._future_events = deferred_events
        heapq.heapify(self._future_events)

        self._publish_step_metrics()


###########################################################################################################################################
# the following lines of code are needed to allow the functions inside traffic_generator.py, monitoring.py and queueing.py
# to be defined in other files while still belonging to the NetworkOrchestrator class.
###########################################################################################################################################

from .traffic_generation import (
    _compute_service_rate_pps,
    _normalize_class_load_fractions,
    _compute_background_arrival_rates_for_node,
    _initialize_background_arrival_rates,
    _draw_poisson_count,
    _draw_arrival_times,
    _generate_background_packets_for_class,
    _generate_all_background_packets_for_node,
    _generate_all_background_traffic,
    _rate_mbps_to_packet_interval_s,
    _create_user_packet,
    _generate_user_packets_cbr,
    _get_vbr_on_fraction,
    _get_vbr_mean_on_off_times,
    _sample_next_vbr_state_duration,
    _initialize_vbr_source_state,
    _generate_user_packets_vbr,
    _generate_user_packets,
)
from .queueing import (
    _is_node_buffer_full,
    _enqueue_common,
    _enqueue_background_packet,
    _enqueue_user_packet,
    _dequeue_head_packet,
    _compute_tx_time_seconds,
    _select_next_priority,
    _start_transmission,
    _complete_transmission,
    _get_node_by_id,
    _maybe_forward_background_packet,
)
from .monitoring import (
    _build_monitor_windows,
    _reset_step_accumulators,
    _get_window_for_time,
    _register_created_user_packet,
    _register_dropped_user_packet,
    _register_user_packet_seen_at_node,
    _register_user_packet_transmitted_from_node,
    _register_arrived_user_packet,
    _compute_window_throughput_mbps,
    _compute_window_latency_summary,
    _compute_window_loss_ratio,
    _aggregate_user_metrics_from_windows,
    _build_route_node_stats,
    _publish_step_metrics,
)

_moved = {
    # traffic generation
    "_compute_service_rate_pps": _compute_service_rate_pps,
    "_normalize_class_load_fractions": _normalize_class_load_fractions,
    "_compute_background_arrival_rates_for_node": _compute_background_arrival_rates_for_node,
    "_initialize_background_arrival_rates": _initialize_background_arrival_rates,
    "_draw_poisson_count": _draw_poisson_count,
    "_draw_arrival_times": _draw_arrival_times,
    "_generate_background_packets_for_class": _generate_background_packets_for_class,
    "_generate_all_background_packets_for_node": _generate_all_background_packets_for_node,
    "_generate_all_background_traffic": _generate_all_background_traffic,
    "_rate_mbps_to_packet_interval_s": _rate_mbps_to_packet_interval_s,
    "_create_user_packet": _create_user_packet,
    "_generate_user_packets_cbr": _generate_user_packets_cbr,
    "_get_vbr_on_fraction": _get_vbr_on_fraction,
    "_get_vbr_mean_on_off_times": _get_vbr_mean_on_off_times,
    "_sample_next_vbr_state_duration": _sample_next_vbr_state_duration,
    "_initialize_vbr_source_state": _initialize_vbr_source_state,
    "_generate_user_packets_vbr": _generate_user_packets_vbr,
    "_generate_user_packets": _generate_user_packets,
    # queueing
    "_is_node_buffer_full": _is_node_buffer_full,
    "_enqueue_common": _enqueue_common,
    "_enqueue_background_packet": _enqueue_background_packet,
    "_enqueue_user_packet": _enqueue_user_packet,
    "_dequeue_head_packet": _dequeue_head_packet,
    "_compute_tx_time_seconds": _compute_tx_time_seconds,
    "_select_next_priority": _select_next_priority,
    "_start_transmission": _start_transmission,
    "_complete_transmission": _complete_transmission,
    "_get_node_by_id": _get_node_by_id,
    "_maybe_forward_background_packet": _maybe_forward_background_packet,
    # monitoring
    "_build_monitor_windows": _build_monitor_windows,
    "_reset_step_accumulators": _reset_step_accumulators,
    "_get_window_for_time": _get_window_for_time,
    "_register_created_user_packet": _register_created_user_packet,
    "_register_dropped_user_packet": _register_dropped_user_packet,
    "_register_user_packet_seen_at_node": _register_user_packet_seen_at_node,
    "_register_user_packet_transmitted_from_node": _register_user_packet_transmitted_from_node,
    "_register_arrived_user_packet": _register_arrived_user_packet,
    "_compute_window_throughput_mbps": _compute_window_throughput_mbps,
    "_compute_window_latency_summary": _compute_window_latency_summary,
    "_compute_window_loss_ratio": _compute_window_loss_ratio,
    "_aggregate_user_metrics_from_windows": _aggregate_user_metrics_from_windows,
    "_build_route_node_stats": _build_route_node_stats,
    "_publish_step_metrics": _publish_step_metrics,
}

for _name, _func in _moved.items():
    setattr(NetworkOrchestrator, _name, _func)
