from __future__ import annotations

from typing import Any, Dict, List
from packetSimulator.network_models import WindowUserMetrics, StepNodeMetrics, NetworkNode, Packet

################################################################################################################
# These functions belong to the NetworkOrchestrator class inside network_orchestrator.py
# They were moved here to make the code easier to navigate
################################################################################################################

def _build_monitor_windows(self, t_start: float, t_end: float) -> List[WindowUserMetrics]:
    """
    Build monitor windows covering [t_start, t_end) with width monitor_interval.
    """
    if t_end < t_start:
        raise ValueError("t_end must be >= t_start")

    monitor_interval = float(self._state.monitor_interval)
    if monitor_interval <= 0.0:
        raise ValueError("monitor_interval must be > 0")

    windows: List[WindowUserMetrics] = []
    current = t_start
    idx = 0

    while current < t_end:
        nxt = min(current + monitor_interval, t_end)
        windows.append(
            WindowUserMetrics(
                window_index=idx,
                start_time=current,
                end_time=nxt,
            )
        )
        current = nxt
        idx += 1

    return windows


def _reset_step_accumulators(self, t_start: float, t_end: float) -> None:
    """
    Reset all monitoring accumulators for one simulation step.
    """
    self._window_stats = self._build_monitor_windows(t_start, t_end)

    self._step_created_count = 0
    self._step_arrived_count = 0
    self._step_drop_count = 0

    self._step_node_metrics = {}
    for node in self._nodes:
        self._step_node_metrics[str(node.node_id)] = StepNodeMetrics(
            node_id=str(node.node_id),
            hop_index=int(node.hop_index),
            include_in_route_stats=bool(getattr(node, "include_in_route_stats", True)),
        )


def _get_window_for_time(self, event_time: float) -> WindowUserMetrics:
    """
    Return the monitor window containing event_time.

    Convention:
    - windows are [start, end)
    - the final window includes its right edge by fallback
    """
    if not self._window_stats:
        raise ValueError("Window stats are not initialized")

    for window in self._window_stats:
        if window.start_time <= event_time < window.end_time:
            return window

    last = self._window_stats[-1]
    if abs(event_time - last.end_time) < 1e-12 or event_time >= last.end_time:
        return last

    raise ValueError(f"Event time {event_time} does not belong to any monitor window")


def _register_created_user_packet(self, packet: "Packet") -> None:
    """
    Register a newly created user-flow packet at the source.
    """
    self._source.packets_created += 1
    self._state.packets_created += 1
    self._step_created_count += 1

    window = self._get_window_for_time(packet.created_at)
    window.packets_created += 1


def _register_dropped_user_packet(self, node: "NetworkNode", packet: "Packet", drop_time: float) -> None:
    """
    Register a user-flow packet dropped at a node.
    """
    self._step_drop_count += 1

    window = self._get_window_for_time(drop_time)
    window.packets_dropped += 1

    node_metric = self._step_node_metrics[str(node.node_id)]
    node_metric.packets_dropped += 1


def _register_user_packet_seen_at_node(
    self,
    node: "NetworkNode",
    was_enqueued: bool,
) -> None:
    """
    Register that a user packet was presented to a node.
    """
    node_metric = self._step_node_metrics[str(node.node_id)]
    node_metric.packets_received += 1
    if was_enqueued:
        node_metric.packets_enqueued += 1


def _register_user_packet_transmitted_from_node(
    self,
    node: "NetworkNode",
    queue_delay_ms: float,
) -> None:
    """
    Register a user packet transmitted from a node.
    """
    node_metric = self._step_node_metrics[str(node.node_id)]
    node_metric.packets_transmitted += 1
    node_metric.queue_delay_samples_ms.append(queue_delay_ms)


def _register_arrived_user_packet(self, packet: "Packet", now: float) -> None:
    """
    Register end-to-end arrival of a user-flow packet at the receiver.
    """
    packet.arrived_at = now

    self._receiver.packets_arrived += 1
    self._receiver.arrived_packet_ids.append(packet.packet_id)
    self._receiver.bytes_received += int(packet.size_bytes)

    latency_ms = (now - packet.created_at) * 1000.0
    self._receiver.latency_samples_ms.append(latency_ms)

    self._state.packets_arrived += 1
    self._step_arrived_count += 1

    window = self._get_window_for_time(now)
    window.packets_arrived += 1
    window.bytes_arrived += int(packet.size_bytes)
    window.latency_samples_ms.append(latency_ms)


def _compute_window_throughput_mbps(self, window: WindowUserMetrics) -> float:
    duration = window.duration_s()
    if duration <= 0.0:
        return 0.0
    return (8.0 * float(window.bytes_arrived)) / duration / 1e6


def _compute_window_latency_summary(self, window: WindowUserMetrics) -> Dict[str, float]:
    samples = window.latency_samples_ms
    if not samples:
        return {"min": 0.0, "avg": 0.0, "max": 0.0}

    return {
        "min": min(samples),
        "avg": sum(samples) / len(samples),
        "max": max(samples),
    }


def _compute_window_loss_ratio(self, window: WindowUserMetrics) -> float:
    if window.packets_created <= 0:
        return 0.0
    return float(window.packets_dropped) / float(window.packets_created)


def _aggregate_user_metrics_from_windows(self) -> None:
    throughput_values = [ self._compute_window_throughput_mbps(w) for w in self._window_stats ]

    if throughput_values:
        self._state.user_measured_throughput = {
            "min": min(throughput_values),
            "avg": sum(throughput_values) / len(throughput_values),
            "max": max(throughput_values),
            "samples": throughput_values,
            "windows": [
                {
                    "windowindex": w.window_index,
                    "starttime": w.start_time,
                    "endtime": w.end_time,
                    "throughputmbps": self._compute_window_throughput_mbps(w),
                }
                for w in self._window_stats
            ],
        }
    else:
        self._state.user_measured_throughput = {"min": 0.0, "avg": 0.0, "max": 0.0, "samples": [], "windows": []}

    valid_latency_windows = [w for w in self._window_stats if w.latency_samples_ms]
    if valid_latency_windows:
        self._state.user_measured_latency = {
            "min": min(self._compute_window_latency_summary(w)["min"] for w in valid_latency_windows),
            "avg": sum(self._compute_window_latency_summary(w)["avg"] for w in valid_latency_windows) / len(valid_latency_windows),
            "max": max(self._compute_window_latency_summary(w)["max"] for w in valid_latency_windows),
            "windows": [
                {
                    "windowindex": w.window_index,
                    "starttime": w.start_time,
                    "endtime": w.end_time,
                    **self._compute_window_latency_summary(w),
                }
                for w in self._window_stats
            ],
        }
    else:
        self._state.user_measured_latency = {
            "min": 0.0,
            "avg": 0.0,
            "max": 0.0,
            "windows": [
                {
                    "windowindex": w.window_index,
                    "starttime": w.start_time,
                    "endtime": w.end_time,
                    "min": 0.0,
                    "avg": 0.0,
                    "max": 0.0,
                }
                for w in self._window_stats
            ],
        }

    total_created_step = sum(w.packets_created for w in self._window_stats)
    total_dropped_step = sum(w.packets_dropped for w in self._window_stats)
    overall_loss = 0.0 if total_created_step <= 0 else total_dropped_step / total_created_step
    
    self._state.user_measured_loss_ratio = {
        "avg": overall_loss,
        "windows": [
            {
                "windowindex": w.window_index,
                "starttime": w.start_time,
                "endtime": w.end_time,
                "loss_ratio": 0.0 if w.packets_created <= 0 else w.packets_dropped / w.packets_created
            }
            for w in self._window_stats
        ]
    }
    


def _build_route_node_stats(self) -> None:
    """
    Build exported route_node_stats from step accumulators.
    Excludes nodes with include_in_route_stats=False, e.g. the source.
    """
    result: List[Dict[str, Any]] = []

    for node in self._nodes:
        node_metric = self._step_node_metrics[str(node.node_id)]

        if not node_metric.include_in_route_stats:
            continue

        samples = node_metric.queue_delay_samples_ms
        if samples:
            avg_q = sum(samples) / len(samples)
            min_q = min(samples)
            max_q = max(samples)
        else:
            avg_q = 0.0
            min_q = 0.0
            max_q = 0.0

        drop_ratio = 0.0
        if node_metric.packets_received > 0:
            drop_ratio = node_metric.packets_dropped / node_metric.packets_received

        node_id_str = str(node.node_id)
        active_links_count = sum(
            1 for link in (self._state.last_links or [])
            if str(link.get("source")) == node_id_str or str(link.get("target")) == node_id_str
        )

        result.append({
            "satelliteid": node_metric.node_id,
            "avgqueueingdelayms": avg_q,
            "minqueueingdelayms": min_q,
            "maxqueueingdelayms": max_q,
            "packetdropratio": drop_ratio,
            "active_isls": active_links_count,
        })

    self._state.route_node_stats = result
    
def _publish_step_metrics(self) -> None:
    """
    Publish all monitoring outputs for the completed simulation step.
    """
    self._aggregate_user_metrics_from_windows()
    self._build_route_node_stats()

    if self._source is not None:
        self._state.packets_created = self._source.packets_created
    else:
        self._state.packets_created = 0

    if self._receiver is not None:
        self._state.packets_arrived = self._receiver.packets_arrived
    else:
        self._state.packets_arrived = 0