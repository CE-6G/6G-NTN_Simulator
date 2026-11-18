from __future__ import annotations

from math import isfinite
from typing import Dict, List
import numpy as np

from packetSimulator.network_models import (
    Packet,
    BackgroundPacket,
    NetworkNode
)

################################################################################################################
# These functions belong to the NetworkOrchestrator class inside network_orchestrator.py
# They were moved here to make the code easier to navigate
################################################################################################################

def _compute_service_rate_pps(self, capacity_mbps: float) -> float:
    """
    Convert link capacity in Mbps to packet service rate in packets/s.
    """
    packet_size_bytes = float(self._state.packet_size_bytes)
    if packet_size_bytes <= 0:
        raise ValueError("packet_size_bytes must be > 0")

    capacity_mbps = float(capacity_mbps)
    if capacity_mbps <= 0:
        return 0.0

    return (capacity_mbps * 1e6) / (8.0 * packet_size_bytes)


def _normalize_class_load_fractions(self) -> Dict[int, float]:
    """
    Read fraction_of_load from the configured priority levels and normalize them.

    Returns:
        dict {priority: normalized_fraction}

    Notes:
        - If no class has fraction_of_load > 0, use a uniform split.
        - Normalization is useful in case frontend values do not sum exactly to 1.
    """
    priority_levels = list(self._state.priority_levels or [])
    if not priority_levels:
        raise ValueError("priority_levels is empty")

    raw: Dict[int, float] = {}
    for item in priority_levels:
        priority = int(item.get("priority", 0))
        frac = float(item.get("fraction_of_load", 0.0))
        raw[priority] = max(0.0, frac)

    total = sum(raw.values())

    if total <= 0.0:
        n = len(raw)
        if n == 0:
            raise ValueError("No priority classes available")
        uniform = 1.0 / n
        return {priority: uniform for priority in raw}

    return {priority: value / total for priority, value in raw.items()}


def _compute_background_arrival_rates_for_node(self, node) -> Dict[int, float]:
    """
    Compute per-class Poisson background arrival rates for one node on the path.

    The target amount of
    background traffic that must exit this node's simulated (path) link is
    node.background_load_mbps, computed by _compute_background_traffic_load
    or _compute_population_weighted_load.

    Part of that target may already be satisfied by packets forwarded in
    from the previous node on the path:
        incoming_mbps = previous_node.background_load_mbps * previous_node.forward_fraction
    (For the source node there is no previous node, so incoming_mbps = 0 and
    the source generates its full target load locally.)

    Only the remainder is generated locally at this node:
        local_generation_mbps = max(0, node.background_load_mbps - incoming_mbps)

    This local generation rate is converted to packets/s and split across
    priority classes using each class's fraction_of_load.
    """
    target_load_mbps = max(0.0, float(getattr(node, "background_load_mbps", 0.0)))

    incoming_mbps = 0.0
    if node.hop_index > 0 and (node.hop_index - 1) < len(self._nodes):
        prev_node = self._nodes[node.hop_index - 1]
        incoming_mbps = max(
            0.0,
            float(getattr(prev_node, "background_load_mbps", 0.0))
            * float(getattr(prev_node, "forward_fraction", 0.0)),
        )

    local_generation_mbps = max(0.0, target_load_mbps - incoming_mbps)

    total_background_rate_pps = self._compute_service_rate_pps(local_generation_mbps)

    raw_fractions = {
        priority: max(0.0, class_state.config.fraction_of_load)
        for priority, class_state in node.class_states.items()
    }

    total_fraction = sum(raw_fractions.values())
    if total_fraction <= 0.0:
        n = len(raw_fractions)
        normalized = {p: 1.0 / n for p in raw_fractions}
    else:
        normalized = {p: f / total_fraction for p, f in raw_fractions.items()}

    return {
        priority: normalized[priority] * total_background_rate_pps
        for priority in node.class_states
    }


def _initialize_background_arrival_rates(self) -> None:
    """
    Compute and store per-class background Poisson rates for every node on
    the path, including the source.
    """
    if not hasattr(self, "_nodes") or self._nodes is None:
        raise ValueError("Network nodes are not initialized")

    for node in self._nodes:
        rates = self._compute_background_arrival_rates_for_node(node)
        for priority, lambda_pps in rates.items():
            node.class_states[priority].background_arrival_rate_pps = lambda_pps


def _draw_poisson_count(self, lambda_pps: float, dt: float, rng: np.random.Generator) -> int:
    """
    Draw the number of background packet arrivals during interval dt.

    Uses Poisson statistics with mean lambda_pps * dt.
    Returns 0 if the rate or interval is negligible to avoid wasting RNG.
    """
    if lambda_pps <= 0.0 or dt <= 0.0:
        return 0

    mean_arrivals = lambda_pps * dt
    return int(rng.poisson(lam=mean_arrivals))


def _draw_arrival_times(
    self,
    count: int,
    t_start: float,
    dt: float,
    rng: np.random.Generator,
) -> List[float]:
    """
    Draw `count` independent uniform arrival times in [t_start, t_start + dt].

    This models the Poisson property that, conditioned on the number of arrivals
    in an interval, their positions are i.i.d. uniform.
    """
    if count == 0:
        return []

    offsets = rng.uniform(0.0, dt, size=count)
    offsets.sort()  # sort so arrivals are processed in chronological order
    return [t_start + o for o in offsets]


def _generate_background_packets_for_class(
    self,
    node: "NetworkNode",
    priority: int,
    t_start: float,
    dt: float,
    rng: np.random.Generator,
) -> List[BackgroundPacket]:
    """
    Generate background packets for one class at one node.

    Returns the list in chronological arrival order.
    """
    class_state = node.class_states.get(priority)
    if class_state is None:
        return []

    lambda_pps = class_state.background_arrival_rate_pps
    count = self._draw_poisson_count(lambda_pps, dt, rng)

    if count == 0:
        return []

    arrival_times = self._draw_arrival_times(count, t_start, dt, rng)

    bg_pkt_size = int(self._state.packet_size_bytes)  # same size as user packets

    packets: List[BackgroundPacket] = []
    for i, arrival_time in enumerate(arrival_times):
        bg_id = class_state.generated_background_packets + i
        pkt = BackgroundPacket(
            bg_packet_id=bg_id,
            priority=priority,
            size_bytes=bg_pkt_size,
            arrival_time=arrival_time,
        )
        packets.append(pkt)

    class_state.generated_background_packets += count
    return packets


def _generate_all_background_packets_for_node(
    self,
    node: "NetworkNode",
    t_start: float,
    dt: float,
    rng: np.random.Generator,
) -> List[BackgroundPacket]:
    """
    Generate background packets for all priority classes at one node,
    merged and sorted chronologically.

    This is the main entry point for background traffic generation at a node
    during a simulation interval [t_start, t_start + dt].
    """
    all_packets: List[BackgroundPacket] = []

    for priority in sorted(node.class_states.keys()):
        class_packets = self._generate_background_packets_for_class(
            node, priority, t_start, dt, rng
        )
        all_packets.extend(class_packets)

    # Sort all classes together by arrival time
    all_packets.sort(key=lambda p: p.arrival_time)
    return all_packets


def _generate_all_background_traffic(
    self,
    t_start: float,
    t_end: float,
    rng: np.random.Generator,
) -> Dict[str, List[BackgroundPacket]]:
    """
    Generate background packets for all forwarding nodes for one simulation
    interval [t_start, t_start + dt].

    Returns:
        dict { node_id: [BackgroundPacket, ...] } in chronological order per node.

    Notes:
        - satellites may receive background traffic from previous nodes in the path. We must take
            these packets into account when creating the background packets for a satellite.
        - The number of packets that arrive from the previous node is given by the background traffic
            in the previous node divided by NumberOfLinks - 1
        - Here we create the packets that will be deleted inside the current node. In the first node 
            of the path (the source), we generate all of them.
    """
    if not hasattr(self, "_nodes") or not self._nodes:
        raise ValueError("Network nodes not initialized. Call _build_network_entities() first.")

    result: Dict[str, List[BackgroundPacket]] = {}

    dt = t_end-t_start

    for node in self._nodes:
        bg_packets = self._generate_all_background_packets_for_node(
            node, t_start, dt, rng
        )
        result[node.node_id] = bg_packets

    return result


def _rate_mbps_to_packet_interval_s(self, rate_mbps: float, packet_size_bytes: int) -> float:
    """
    Convert a bit rate in Mbps into the inter-packet interval for fixed-size packets.
    """
    rate_mbps = float(rate_mbps)
    packet_size_bytes = int(packet_size_bytes)

    if rate_mbps <= 0.0:
        return float("inf")
    if packet_size_bytes <= 0:
        raise ValueError("packet_size_bytes must be > 0")

    pps = (rate_mbps * 1e6) / (8.0 * packet_size_bytes)
    if pps <= 0.0:
        return float("inf")

    return 1.0 / pps

def _create_user_packet(self, created_at: float) -> "Packet":
    """
    Create one user-flow packet at the source.
    """
    pkt = Packet(
        packet_id=self._source.next_packet_id,
        flow_id=self._source.flow_id,
        priority=int(self._source.user_priority),
        size_bytes=int(self._source.packet_size_bytes),
        created_at=float(created_at),
        current_hop=0,
    )

    self._source.next_packet_id += 1
    self._register_created_user_packet(pkt)
    return pkt


def _generate_user_packets_cbr(self, t_start: float, t_end: float) -> List["Packet"]:
    """
    Generate CBR user packets in [t_start, t_end).
    """
    packets: List[Packet] = []

    interval_s = self._rate_mbps_to_packet_interval_s(
        rate_mbps=float(self._source.avg_rate_mbps),
        packet_size_bytes=int(self._source.packet_size_bytes),
    )

    if not isfinite(interval_s):
        self._source.next_generation_time = max(self._source.next_generation_time, t_end)
        return packets

    if self._source.next_generation_time <= 0.0:
        self._source.next_generation_time = t_start

    while self._source.next_generation_time < t_end:
        if self._source.next_generation_time >= t_start:
            pkt = self._create_user_packet(self._source.next_generation_time)
            packets.append(pkt)

        self._source.next_generation_time += interval_s

    return packets


def _get_vbr_on_fraction(self) -> float:
    """
    Long-term ON fraction needed to match avg_rate from peak_rate.
    """
    peak = max(0.0, float(self._source.peak_rate_mbps))
    avg = max(0.0, float(self._source.avg_rate_mbps))

    if peak <= 0.0:
        return 0.0

    return max(0.0, min(1.0, avg / peak))


def _get_vbr_mean_on_off_times(self) -> tuple[float, float]:
    """
    Map burstiness to mean ON/OFF durations while preserving avg/peak ratio.
    """
    burstiness = max(0.0, min(1.0, float(self._source.burstiness)))
    on_fraction = self._get_vbr_on_fraction()

    if on_fraction <= 0.0:
        return 0.0, float("inf")
    if on_fraction >= 1.0:
        return float("inf"), 0.0

    base_time = 1.0
    mean_on = base_time * (0.5 + 2.0 * burstiness)
    mean_off = mean_on * (1.0 - on_fraction) / on_fraction

    return mean_on, mean_off


def _sample_next_vbr_state_duration(self, is_on: bool) -> float:
    """
    Sample duration of the current ON or OFF state.
    """
    mean_on, mean_off = self._get_vbr_mean_on_off_times()

    if is_on:
        if not isfinite(mean_on) or mean_on <= 0.0:
            return float("inf")
        return float(self._rng.exponential(mean_on))
    else:
        if not isfinite(mean_off) or mean_off <= 0.0:
            return float("inf")
        return float(self._rng.exponential(mean_off))


def _initialize_vbr_source_state(self, t0: float) -> None:
    """
    Initialize VBR generator state on first use.
    """
    if self._source.next_generation_time <= 0.0:
        self._source.next_generation_time = t0

    if self._source.next_state_change_time <= 0.0:
        self._source.vbr_is_on = True
        duration = self._sample_next_vbr_state_duration(is_on=True)
        self._source.next_state_change_time = t0 + duration


def _generate_user_packets_vbr(self, t_start: float, t_end: float) -> List["Packet"]:
    """
    Generate VBR user packets in [t_start, t_end) using an ON/OFF source.

    ON state:
        deterministic packet stream at peak_rate_mbps
    OFF state:
        no packet generation
    """
    packets: List[Packet] = []

    self._initialize_vbr_source_state(t_start)

    peak_interval_s = self._rate_mbps_to_packet_interval_s(
        rate_mbps=float(self._source.peak_rate_mbps),
        packet_size_bytes=int(self._source.packet_size_bytes),
    )

    while t_start < t_end:
        if t_start >= self._source.next_state_change_time:
            self._source.vbr_is_on = not self._source.vbr_is_on
            duration = self._sample_next_vbr_state_duration(self._source.vbr_is_on)
            self._source.next_state_change_time = t_start + duration

            if self._source.next_generation_time < t_start:
                self._source.next_generation_time = t_start

        segment_end = min(t_end, self._source.next_state_change_time)

        if self._source.vbr_is_on and isfinite(peak_interval_s):
            if self._source.next_generation_time < t_start:
                self._source.next_generation_time = t_start

            while self._source.next_generation_time < segment_end:
                pkt = self._create_user_packet(self._source.next_generation_time)
                packets.append(pkt)
                self._source.next_generation_time += peak_interval_s
        else:
            if self._source.next_generation_time < segment_end:
                self._source.next_generation_time = segment_end

        t_start = segment_end

    return packets


def _generate_user_packets(self, t_start: float, t_end: float) -> List["Packet"]:
    """
    Generate user-flow packets in [t_start, t_end) according to the configured
    source traffic model.
    """
    if t_end < t_start:
        raise ValueError("t_end must be >= t_start")

    mode = str(self._source.traffic_mode).strip().upper()

    if mode == "CBR":
        return self._generate_user_packets_cbr(t_start, t_end)

    if mode == "VBR":
        return self._generate_user_packets_vbr(t_start, t_end)

    raise ValueError(f"Unsupported traffic mode: {self._source.traffic_mode!r}")
