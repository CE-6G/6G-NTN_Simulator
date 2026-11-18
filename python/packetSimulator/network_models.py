from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from typing import Dict, List, Optional


@dataclass
class Packet:
    packet_id: int
    flow_id: str
    priority: int
    size_bytes: int
    created_at: float
    current_hop: int = 0
    enqueue_time: Optional[float] = None
    tx_start_time: Optional[float] = None
    arrived_at: Optional[float] = None
    dropped: bool = False


@dataclass
class ClassConfig:
    priority: int
    scheduling: str
    weight: int
    label: str = ""
    fraction_of_load: float = 0.0


@dataclass
class ClassQueueState:
    config: ClassConfig
    queue: deque = field(default_factory=deque)
    background_arrival_rate_pps: float = 0.0
    generated_background_packets: int = 0
    served_packets: int = 0
    dropped_packets: int = 0


@dataclass
class NodeCounters:
    packets_received: int = 0
    background_packets_received_from_previous_node: int = 0
    packets_enqueued: int = 0
    packets_transmitted: int = 0
    packets_dropped: int = 0
    queue_delay_samples_ms: List[float] = field(default_factory=list)


@dataclass
class NetworkNode:
    node_id: str
    hop_index: int
    buffer_size_packets: int
    link_capacity_mbps: float
    link_delay_ms: float
    scheduler_kind: str = "HYBRID"
    node_role: str = "forwarder"  # "source" or "forwarder"
    include_in_route_stats: bool = True
    is_access_node: bool = False
    total_buffer_occupancy: int = 0
    transmitter_busy: bool = False
    rr_pointer: int = 0
    wrr_deficit: Dict[int, int] = field(default_factory=dict)
    class_states: Dict[int, ClassQueueState] = field(default_factory=dict)
    counters: NodeCounters = field(default_factory=NodeCounters)
    # Diffusion-model background traffic
    background_load_mbps: float = 0.0
    # Probability that a served background packet is forwarded to the next
    # path node instead of being absorbed
    forward_fraction: float = 0.0


@dataclass
class SourceState:
    node_id: str
    flow_id: str
    user_priority: int
    packet_size_bytes: int
    traffic_mode: str
    peak_rate_mbps: float
    avg_rate_mbps: float
    burstiness: float
    next_packet_id: int = 0
    packets_created: int = 0

    # generator state
    next_generation_time: float = 0.0
    vbr_is_on: bool = True
    next_state_change_time: float = 0.0

@dataclass
class ReceiverState:
    node_id: str
    packets_arrived: int = 0
    latency_samples_ms: List[float] = field(default_factory=list)
    arrived_packet_ids: List[int] = field(default_factory=list)
    bytes_received: int = 0


@dataclass
class BackgroundPacket:
    """
    Represents one background (cross-traffic) packet at a forwarding node.
    Background packets are not tracked end-to-end (unless they flow on the path of our flow of interest); 
    they exist only to occupy buffer space and consume service time at one specific node.
    """
    bg_packet_id: int
    priority: int
    size_bytes: int
    arrival_time: float          # simulated arrival time within the interval
    enqueue_time: Optional[float] = None
    dropped: bool = False


@dataclass
class WindowUserMetrics:
    window_index: int
    start_time: float
    end_time: float

    packets_created: int = 0
    packets_dropped: int = 0
    packets_arrived: int = 0
    bytes_arrived: int = 0

    latency_samples_ms: List[float] = field(default_factory=list)

    def duration_s(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class StepNodeMetrics:
    node_id: str
    hop_index: int
    include_in_route_stats: bool

    packets_received: int = 0
    packets_enqueued: int = 0
    packets_transmitted: int = 0
    packets_dropped: int = 0
    queue_delay_samples_ms: List[float] = field(default_factory=list)