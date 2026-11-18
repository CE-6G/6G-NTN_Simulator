from __future__ import annotations

from typing import Any, Optional, Dict
import logging

from packetSimulator.network_models import Packet, NetworkNode, BackgroundPacket

logger = logging.getLogger(__name__)

################################################################################################################
# These functions belong to the NetworkOrchestrator class inside network_orchestrator.py
# They were moved here to make the code easier to navigate
################################################################################################################

def _is_node_buffer_full(self, node: "NetworkNode") -> bool:
        return node.total_buffer_occupancy >= int(node.buffer_size_packets)

def _enqueue_common(
    self,
    node: "NetworkNode",
    packet,
    now: float,
    is_user_packet: bool,
) -> bool:
    priority = int(packet.priority)

    if priority not in node.class_states:
        raise ValueError(
            f"Node {node.node_id}: priority class {priority} is not configured"
        )

    class_state = node.class_states[priority]

    if is_user_packet:
        node.counters.packets_received += 1

    if self._is_node_buffer_full(node):
        packet.dropped = True

        if is_user_packet:
            node.counters.packets_dropped += 1
        else:
            class_state.dropped_packets += 1

        return False

    packet.enqueue_time = now

    class_state.queue.append(packet)
    node.total_buffer_occupancy += 1

    if is_user_packet:
        node.counters.packets_enqueued += 1

    return True


def _enqueue_background_packet(
    self,
    node: "NetworkNode",
    packet: "BackgroundPacket",
    now: float,
) -> bool:
    """
    Enqueue one background packet into the node.

    Background packets:
    - consume shared buffer space
    - compete in scheduler queues
    - do not contribute to user end-to-end KPIs
    - do contribute to background class-local drop counters
    """
    return self._enqueue_common(
        node=node,
        packet=packet,
        now=now,
        is_user_packet=False,
    )


def _enqueue_user_packet(
    self,
    node: "NetworkNode",
    packet: "Packet",
    now: float,
) -> bool:
    """
    Enqueue one user packet into the node.

    User packets:
    - consume shared buffer space
    - compete in scheduler queues
    - contribute to node counters used for route_node_stats
    - may later contribute to packets_arrived, latency, throughput, loss
    """
    return self._enqueue_common(
        node=node,
        packet=packet,
        now=now,
        is_user_packet=True,
    )


def _dequeue_head_packet(self, node: "NetworkNode", priority: int):
    """
    Remove the HOL packet from a class queue and update shared occupancy.
    """
    if priority not in node.class_states:
        raise ValueError(f"Node {node.node_id}: unknown priority {priority}")

    q = node.class_states[priority].queue
    if not q:
        return None

    packet = q.popleft()
    node.total_buffer_occupancy -= 1

    if node.total_buffer_occupancy < 0:
        node.total_buffer_occupancy = 0
        raise RuntimeError(f"Node {node.node_id}: negative buffer occupancy")

    return packet


def _compute_tx_time_seconds(self, packet_size_bytes: int, capacity_mbps: float) -> float:
    """
    Serialization time of one packet on a link.

    tx_time = packet_bits / link_rate_bits_per_second
    """
    if packet_size_bytes <= 0:
        raise ValueError("packet_size_bytes must be > 0")
    if capacity_mbps <= 0:
        raise ValueError("capacity_mbps must be > 0")

    return (8.0 * float(packet_size_bytes)) / (float(capacity_mbps) * 1e6)


def _select_next_priority(self, node: "NetworkNode") -> Optional[int]:
    """
    Select the next priority class to serve on this node.

    Policy:
    - Strict priority (SP) classes always win if non-empty.
    - Otherwise, weighted round robin (WRR) among WRR classes.
    - Lower numeric priority means higher scheduling precedence.
    """
    nonempty_priorities = [
        p for p, class_state in sorted(node.class_states.items())
        if len(class_state.queue) > 0
    ]

    if not nonempty_priorities:
        return None

    # 1) Strict-priority classes first
    sp_candidates = [
        p for p in nonempty_priorities
        if node.class_states[p].config.scheduling.upper() == "SP"
    ]
    if sp_candidates:
        return min(sp_candidates)

    # 2) WRR among non-empty WRR classes
    wrr_candidates = [
        p for p in nonempty_priorities
        if node.class_states[p].config.scheduling.upper() == "WRR"
    ]
    if not wrr_candidates:
        return None

    n = len(wrr_candidates)
    start_idx = node.rr_pointer % n

    for offset in range(n):
        idx = (start_idx + offset) % n
        p = wrr_candidates[idx]
        class_state = node.class_states[p]
        weight = max(1, int(class_state.config.weight))

        if len(class_state.queue) == 0:
            node.wrr_deficit[p] = 0
            continue

        deficit = node.wrr_deficit.get(p, 0)
        if deficit <= 0:
            deficit = weight

        node.wrr_deficit[p] = deficit - 1

        # Stay on same class if it can continue next time
        if node.wrr_deficit[p] > 0 and len(class_state.queue) > 0:
            node.rr_pointer = idx
        else:
            node.rr_pointer = (idx + 1) % n

        return p

    return None


def _start_transmission(self, node: "NetworkNode", now: float) -> Optional[Dict[str, Any]]:
    if node.transmitter_busy:
        return None

    priority = self._select_next_priority(node)
    if priority is None:
        return None

    packet = self._dequeue_head_packet(node, priority)
    if packet is None:
        return None

    node.transmitter_busy = True
    packet.tx_start_time = now

    enqueue_time = getattr(packet, "enqueue_time", None)
    if enqueue_time is None:
        queue_delay_ms = 0.0
    else:
        queue_delay_ms = max(0.0, (now - enqueue_time) * 1000.0)

    is_user_packet = isinstance(packet, Packet)

    if is_user_packet:
        node.counters.queue_delay_samples_ms.append(queue_delay_ms)
        node.counters.packets_transmitted += 1
    else:
        node.class_states[priority].served_packets += 1

    tx_time_s = self._compute_tx_time_seconds(
        packet_size_bytes=int(packet.size_bytes),
        capacity_mbps=node.link_capacity_mbps,
    )
    tx_end_time = now + tx_time_s

    return {
        "event_type": "tx_complete",
        "node_id": node.node_id,
        "node_hop_index": node.hop_index,
        "priority": priority,
        "packet": packet,
        "tx_start_time": now,
        "tx_end_time": tx_end_time,
        "queue_delay_ms": queue_delay_ms,
        "is_user_packet": is_user_packet,
    }


def _maybe_forward_background_packet(
    self,
    node: "NetworkNode",
    packet: "BackgroundPacket",
    tx_end_time: float,
) -> Optional[Dict[str, Any]]:
    """
    Live random-sampling decision, performed at the moment a background
    packet finishes transmission at `node`, on whether to forward it to the
    next node on the path.

    The packet may have been generated locally at this node or forwarded-in
    from a previous node: no distinction is made, since packets are served
    from the combined buffer pool. The forwarding probability is
    node.forward_fraction (1.0 for the source, 1/(M-1) for intermediate
    satellites, 0.0 for the last path node).
    """
    next_hop_index = int(node.hop_index) + 1
    if next_hop_index >= len(self._nodes):
        return None

    forward_fraction = float(getattr(node, "forward_fraction", 0.0))
    if forward_fraction <= 0.0:
        return None

    if self._rng.random() >= forward_fraction:
        return None

    if node.hop_index < 0 or node.hop_index >= len(self._path_links):
        return None

    hop_link = self._path_links[node.hop_index]
    propagation_delay_s = float(hop_link["delay_ms"]) / 1000.0
    arrival_time = tx_end_time + propagation_delay_s

    next_node_id = self._nodes[next_hop_index].node_id

    return {
        "event_type": "bg_arrival",
        "packet": packet,
        "arrival_time": arrival_time,
        "node_id": next_node_id,
    }


def _complete_transmission(
    self,
    node: "NetworkNode",
    packet,
    tx_end_time: float,
) -> Optional[Dict[str, Any]]:
    node.transmitter_busy = False

    is_user_packet = isinstance(packet, Packet)

    if not is_user_packet:
        return self._maybe_forward_background_packet(node, packet, tx_end_time)

    current_hop = int(packet.current_hop)

    if len(self._path_links) == 0:
        logger.info("Outage drop: Connection lost during active packet transmission hop.")
        return None

    if current_hop < 0 or current_hop >= len(self._path_links):
        logger.warning("Invalid packet hop state: current_hop=%d for path length %d. Dropping packet.", current_hop, len(self._path_links))
        return None

    hop_link = self._path_links[current_hop]

    propagation_delay_s = float(hop_link["delay_ms"]) / 1000.0
    arrival_time = tx_end_time + propagation_delay_s

    packet.current_hop += 1

    path = list(self._state.last_route.path)

    if current_hop == len(self._path_links) - 1:
        return {
            "event_type": "receiver_arrival",
            "packet": packet,
            "arrival_time": arrival_time,
            "receiver_id": self._receiver.node_id,
        }

    next_node_id = str(path[current_hop + 1])

    return {
        "event_type": "node_arrival",
        "packet": packet,
        "arrival_time": arrival_time,
        "next_node_id": next_node_id,
        "next_node_path_index": current_hop + 1,
    }


def _get_node_by_id(self, node_id: str) -> "NetworkNode":
    for node in self._nodes:
        if str(node.node_id) == str(node_id):
            return node
    raise ValueError(f"No forwarding node found with node_id={node_id!r}")
