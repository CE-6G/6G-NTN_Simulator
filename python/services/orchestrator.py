"""Python orchestration logic coordinating Cesium polling and MATLAB calls.

This is the BRAIN of the system. It holds all simulation state and contains
the Type-1/Type-2 decision logic that balances performance vs accuracy.
"""


from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Optional
from typing import List, Tuple

from algorithms.dijkstra_router import CostMetric, RouteResult
from packetSimulator.network_orchestrator import NetworkOrchestrator
from utils.matlab_bridge import MatlabBridge
import math

# -------------------------------------------------------------------
# SimulationState
# -------------------------------------------------------------------


@dataclass
class SimulationState:
    """Mutable simulation session state shared by API handlers."""

    initialized: bool = False
    tle_file: str = ""
    simulation_start_time: float = 0.0
    ref_time_current: float = 0.0
    ref_time_previous: float = 0.0

    # Routing endpoints
    source: str = "0"
    destination: str = "1"
    metric: CostMetric = CostMetric.MIN_HOP
    handover_mode: str = "margin"

    # Type-2 validity tracking
    link_valid_until: float = 0.0   # When current links expire
    last_type2_at: float = 0.0

    # Cached data from MATLAB
    last_positions: Dict[str, Any] = field(default_factory=dict)
    last_links: list[Dict[str, Any]] = field(default_factory=list)
    matlab_info: Dict[str, Any] = field(default_factory=dict)
    ground_nodes: List[Dict[str, Any]] = field(default_factory=list)

    # Handover tracking
    handover_count: int = 0

    # snr history: key is "source:target" string, value is deque of (timestamp, snr_db)
    snr_history: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    last_forced_type2_at: float = 0.0

    # network-layer states
    last_route: Optional[RouteResult] = None

    # Network parameters
    buffer_size: float = 256
    packet_size_bytes: float = 1500
    monitor_interval: float = 1
    priority_levels: List[Dict[str, Any]] = field(default_factory=list)

    # Traffic parameters
    user_flow_priority: float = 1.0
    user_flow_cbr_vbr: str = "CBR"
    user_flow_peak_rate: float = 5.0
    user_flow_avg_rate: float = 5.0
    user_flow_burstiness: float = 0.0
    background_traffic_mbps: float = 0.0

    # KPI outputs (populated by packet simulator)
    user_measured_throughput: Dict[str, Any] = field(default_factory=dict)
    user_measured_latency: Dict[str, Any] = field(default_factory=dict)
    user_measured_loss_ratio: float = 0.0
    packets_created: float = 0
    packets_arrived: float = 0
    route_node_stats: List[Dict[str, Any]] = field(default_factory=list)
    background_traffic_injection_type: str = "source_side_only"


# -------------------------------------------------------------------
# SimulationOrchestrator
# -------------------------------------------------------------------

class SimulationOrchestrator:
    """Implements the two-call MATLAB policy requested for Cesium updates."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._bridge = MatlabBridge()
        # Defer connecting to the MATLAB engine until simulation init to avoid
        # blocking the API server startup.
        self._state = SimulationState()
        
        # SNR trigger configuration (margin mode)        
        self._snr_short_window_s = 5.0
        self._snr_long_window_s = 20.0
        self._snr_required_margin_db = 3.0
        self._snr_safety_margin_db = 2.0
        self._snr_drop_rate_db = 1.0
        self._snr_hysteresis_db = 3.0
        self._snr_time_to_trigger_s = 4.0
        self._snr_min_hold_after_handover_s = 5.0
        self._snr_cooldown_after_forced_s = 3.0

        # Network orchestrator (packet-level simulation)
        self._network_orchestrator = NetworkOrchestrator(self._state)


    # ------------------------------------------------------------------
    # SNR History Management
    # ------------------------------------------------------------------

    def _update_snr_history(self, links: List[Dict[str, Any]], now_ts: float) -> None:
        """Store recent SNR samples per link (both directions) as (timestamp, snr_db)."""
        for link in links:
            src = str(link.get("source", ""))
            dst = str(link.get("target", ""))
            if "snr_db" not in link:
                continue
            try:
                snr = float(link.get("snr_db", 0.0))
            except Exception:
                continue

            # Store in both directions for easy lookup
            key_f = f"{src}:{dst}"
            key_r = f"{dst}:{src}"
            # append sample for both directions so lookups are easier
            self._state.snr_history.setdefault(key_f, []).append((now_ts, snr))
            self._state.snr_history.setdefault(key_r, []).append((now_ts, snr))
            
            # Keep only last 10 samples
            for k in (key_f, key_r):
                samples = self._state.snr_history.get(k, [])
                if len(samples) > 10:
                    # keep last 10
                    self._state.snr_history[k] = samples[-10:]

    def _get_recent_stats(self, key: str, now_ts: float) -> Tuple[Optional[float], Optional[float]]:
        """Return (short_avg, long_avg) for a link key, or (None, None) if no data."""
        samples = self._state.snr_history.get(key, [])
        if not samples:
            return None, None
        short_thresh = now_ts - self._snr_short_window_s
        long_thresh = now_ts - self._snr_long_window_s
        short_vals = [v for (t, v) in samples if t >= short_thresh]
        long_vals = [v for (t, v) in samples if t >= long_thresh]
        short_avg = float(sum(short_vals) / len(short_vals)) if short_vals else None
        long_avg = float(sum(long_vals) / len(long_vals)) if long_vals else None
        return short_avg, long_avg

    def _path_worst_snr(self, path: List[str], now_ts: float) -> Optional[float]:
        """Return the worst (min) latest SNR observed along a path, or None if unknown."""
        if not path or len(path) < 2:
            return None
        worst = None
        for i in range(len(path) - 1):
            a = str(path[i])
            b = str(path[i + 1])
            key = f"{a}:{b}"
            samples = self._state.snr_history.get(key, [])
            if not samples:
                return None
            # take most recent sample
            latest = samples[-1][1]
            if worst is None or latest < worst:
                worst = latest
        return worst

    # ------------------------------------------------------------------
    # Ground Links Generator (Fallback)
    # ------------------------------------------------------------------

    def _generate_ground_links(self, ground_nodes: List[Dict[str, Any]], satellites: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create ground-to-satellite links based on proximity.
        Each ground node connects to nearest 2 satellites.
        Used when MATLAB doesn't provide ground links.
        """
        if not ground_nodes or not satellites:
            return []
        # convert satellites to a map of id->position
        # Build satellite position map
        sat_pos = {}
        for s in satellites:
            try:
                sid = str(s.get("id", ""))
                x = float(s.get("x_km", 0.0))
                y = float(s.get("y_km", 0.0))
                z = float(s.get("z_km", 0.0))
                sat_pos[sid] = (x, y, z)
            except Exception:
                continue

        out_links: List[Dict[str, Any]] = []
        for g in ground_nodes:
            gid = str(g.get("node_id") or g.get("id") or g.get("nodeId") or "")
            try:
                lat = float(g.get("lat_deg", g.get("lat", 0.0)))
                lon = float(g.get("lon_deg", g.get("lon", 0.0)))
            except Exception:
                continue
            # convert lat/lon (deg) to ECEF km (approximate)
            R = 6378.0
            lat_r = math.radians(lat)
            lon_r = math.radians(lon)
            gx = R * math.cos(lat_r) * math.cos(lon_r)
            gy = R * math.cos(lat_r) * math.sin(lon_r)
            gz = R * math.sin(lat_r)

            # find nearest satellites
            dists = []
            for sid, (sx, sy, sz) in sat_pos.items():
                dist = math.sqrt((gx - sx) ** 2 + (gy - sy) ** 2 + (gz - sz) ** 2)
                dists.append((dist, sid))
            dists.sort(key=lambda t: t[0])

            # connect to up to 2 nearest satellites
            for dist, sid in dists[:2]:
                distance_km = float(dist)
                delay_ms = distance_km / self._network_orchestrator.topology.config.speed_of_light_km_per_ms
                capacity_mbps = max(25.0, 1200.0 - (distance_km * 0.08))
                out_links.append({
                    "source": gid,
                    "target": sid,
                    "delay_ms": round(delay_ms, 6),
                    "distance_km": round(distance_km, 6),
                    "capacity_mbps": round(capacity_mbps, 3),
                })

        return out_links

    def _evaluate_strict_snr_trigger(self, now_ts: float) -> Tuple[bool, Optional[str]]:
        """Strict handover mode: trigger when any latest sample falls below threshold."""
        strict = getattr(self, "_snr_strict_threshold_db", 0.0)
        for samples in self._state.snr_history.values():
            if not samples:
                continue
            latest = samples[-1][1]
            if latest < strict:
                return True, "snr_strict_below_threshold"
        if self._state.last_route is None:
            return True, "no_route"
        return False, None

    def _evaluate_margin_snr_trigger(self, now_ts: float) -> Tuple[bool, Optional[str]]:
        """Margin mode: trigger based on trends, not instant values.
        Checks:
        1. Sustained low margin (>4 seconds)
        2. Rapid degradation (short vs long window)
        3. Alternative route significantly better
        """
        if self._state.last_route is None:
            return True, "no_route"

        now_path = self._state.last_route.path if self._state.last_route.success else []
        current_worst = self._path_worst_snr(now_path, now_ts)

        # Find worst link on current paths
        worst_link_key = None
        if now_path and len(now_path) >= 2:
            min_snr = None
            for i in range(len(now_path) - 1):
                k = f"{now_path[i]}:{now_path[i + 1]}"
                samples = self._state.snr_history.get(k, [])
                if not samples:
                    continue
                latest = samples[-1][1]
                if min_snr is None or latest < min_snr:
                    min_snr = latest
                    worst_link_key = k

        # Check sustained low margin
        if worst_link_key:
            short_avg, long_avg = self._get_recent_stats(worst_link_key, now_ts)
            if short_avg is not None and long_avg is not None:
                threshold = self._snr_required_margin_db + self._snr_safety_margin_db

                # Sustained below threshold
                if short_avg < threshold:
                    samples = [t for (t, v) in self._state.snr_history.get(worst_link_key, []) if v < threshold]
                    if samples and (now_ts - samples[0] >= self._snr_time_to_trigger_s):
                        return True, "snr_below_margin_sustained"
                    
                 # Rapid degradation    
                if (short_avg - long_avg) < -self._snr_drop_rate_db:
                    return True, "snr_trend_drop"
        
        # Check if alternative route is significantly better
        try:
            alt_route = self._network_orchestrator.compute_route()
            if alt_route and alt_route.success:
                alt_worst = self._path_worst_snr(alt_route.path, now_ts)
                if alt_worst is not None and current_worst is not None and alt_worst > (current_worst + self._snr_hysteresis_db):
                    return True, "alternative_route_better"
        except Exception:
            pass

        return False, None

    def _evaluate_snr_trigger(self, now_ts: float) -> Tuple[bool, Optional[str]]:
        """Decide whether SNR history suggests forcing a Type-2 update.

        Returns (triggered, reason).
        Route to correct handover mode
        """
        if self._state.handover_mode == "strict":
            return self._evaluate_strict_snr_trigger(now_ts)
        return self._evaluate_margin_snr_trigger(now_ts)
    
    def _extract_src_dst_for_initialization(self, ground_nodes, source, destination) -> Dict:
        """Extract source and destination from list of ground nodes"""
        # Note: this function is not used anymore
        for node in ground_nodes:
            if str(node.get("node_id","")) == str(source):
                src = node
            if str(node.get("node_id","")) == str(destination):
                dst = node
        return {"source": src, "destination": dst}

    @property
    def is_ready(self) -> bool:
        return self._state.initialized

# ------------------------------------------------------------------
# Initialize - Setup Simulation
# ------------------------------------------------------------------

    def initialize(
        self,
        tle_file: str,
        simulation_start_time: float,
        source: str,
        destination: str,
        metric: CostMetric,
        handover_mode: str,
        ground_nodes: Optional[List[Dict[str, Any]]],
        buffer_size: float,
        packet_size_bytes: float,
        monitor_interval: float,
        priority_levels: Optional[List[Dict[str, Any]]],
        user_flow_priority: float,
        user_flow_cbr_vbr: str,
        user_flow_peak_rate: float,
        user_flow_avg_rate: float,
        user_flow_burstiness: float,
        background_traffic_mbps: float,
        background_traffic_injection_type: str
    ) -> Dict[str, Any]:
        
        """
        Initialize simulation context from UI parameters.

        Called once from /api/init.
        Connects to MATLAB, loads TLE, computes first route.
        """
        with self._lock:   
            
            # ---------- Connect to MATLAB ----------
            # Ensure the MATLAB engine is connected when initializing the simulation.
            try:
                connected = self._bridge.connect()
                if not connected:
                    print("WARNING: MATLAB engine connection failed - will use Python fallback")
            except Exception as e:
                print(f"ERROR connecting MATLAB bridge: {e}")
            
            # ---------- Set simulation start time ----------
            # Interpret non-positive start times (e.g. 0) as 'now' for convenience
            if simulation_start_time is None or float(simulation_start_time) < 0.0:
                simulation_start_time = time.time()

            # ---------- Initialize MATLAB with TLE and UEs ----------    
            matlab_reply = self._bridge.initialize_simulation(tle_file, simulation_start_time, ground_nodes)

            # ---------- Normalize handover mode ----------
            normalized_mode = (handover_mode or "margin").strip().lower()
            if normalized_mode not in {"strict", "margin"}:
                normalized_mode = "margin"

            # ---------- Create new state ----------
            self._state = SimulationState(
                initialized=True,
                tle_file=tle_file,
                simulation_start_time=float(simulation_start_time),
                source=str(source),
                destination=str(destination),
                metric=metric,
                handover_mode=normalized_mode,
                link_valid_until=0.0,
                last_type2_at=0.0,
                ground_nodes=list(ground_nodes or []),
                buffer_size = buffer_size,
                packet_size_bytes = packet_size_bytes,
                monitor_interval = monitor_interval,
                priority_levels = priority_levels,
                user_flow_priority = user_flow_priority,
                user_flow_cbr_vbr = user_flow_cbr_vbr,
                user_flow_peak_rate = user_flow_peak_rate,
                user_flow_avg_rate = user_flow_avg_rate,
                user_flow_burstiness = user_flow_burstiness,
                background_traffic_mbps = float(background_traffic_mbps),
                background_traffic_injection_type = str(background_traffic_injection_type)
            )
            self._network_orchestrator = NetworkOrchestrator(self._state)
            # keep last MATLAB init reply for frontend visibility
            try:
                self._state.matlab_info = dict(matlab_reply or {})
            except Exception:
                self._state.matlab_info = {}

            # ---------- Initial Type-2 call ----------   
            # Perform an initial Type-2 call to populate positions/links/route
            # so the dashboard can immediately show the full constellation
            # and computed route instead of relying on the first poll.
            try:
                now_ref = float(self._state.simulation_start_time)
                self._state.ref_time_current = now_ref
                self._state.ref_time_previous = now_ref - self._state.monitor_interval

                # Call MATLAB for full link budget
                matlab_payload = self._bridge.type2_link_budget(now_ref)
                self._state.last_positions = matlab_payload
                self._state.last_links = list(matlab_payload.get("links", []))

                # update snr history with initial samples if available
                try:
                    self._update_snr_history(self._state.last_links, now_ref)
                except Exception:
                    pass

                # Set validity window
                valid_for_s = float(matlab_payload.get("link_valid_for_seconds", 20.0))
                self._state.link_valid_until = now_ref + max(2.0, valid_for_s)

                # Compute first route
                route = None
                if self._state.source != self._state.destination:
                    route = self._network_orchestrator.compute_route()
                    self._state.last_route = route
                    if route is not None:
                        # run the network simulator
                        self._network_orchestrator.run_simulation()
            except Exception:
                # If the Type-2 call fails, leave state as-is; subsequent
                # update() calls will attempt Type-2 when needed.
                pass
            
             # ---------- Return initial state ----------
            return {
                "ok": True,
                "matlab": matlab_reply,
                "next_type2_after": self._state.link_valid_until,
                "state": {
                    "source": self._state.source,
                    "destination": self._state.destination,
                    "metric": self._state.metric.value,
                    "handover_mode": self._state.handover_mode,
                },
                "reference_time": now_ref,
                "positions": {
                    "timestamp": self._state.last_positions.get("timestamp", now_ref),
                    "satellites": self._state.last_positions.get("satellites", []),
                    "links": self._state.last_positions.get("links", self._state.last_links),
                    "link_valid_for_seconds": self._state.last_positions.get("link_valid_for_seconds", max(0.0, self._state.link_valid_until - now_ref)),
                },
                "route": None
                if route is None
                else {
                    "source": route.source,
                    "destination": route.destination,
                    "path": route.path,
                    "hop_count": route.hop_count,
                    "total_cost": route.total_cost,
                    "metric": route.metric,
                    "success": route.success,
                    "error": route.error,
                },
                "user_measured_throughput": self._state.user_measured_throughput,
                "user_measured_latency": self._state.user_measured_latency,
                "user_measured_loss_ratio": self._state.user_measured_loss_ratio,          
                "packets_created": self._state.packets_created,
                "packets_arrived": self._state.packets_arrived,
                "route_node_stats": self._state.route_node_stats,
            }


    # ------------------------------------------------------------------
    # Update - (Type-1/Type-2 Decision)
    # ------------------------------------------------------------------

    def update(self, reference_time: Optional[float], config_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Serve one polling step with Type-1/Type-2 decision logic.
        This is the CORE of the system. Called every second from frontend.
        Decision:
        - Type-2: full recompute (links + route) if:
            a) No route exists
            b) Link validity expired (now >= link_valid_until)
            c) SNR trigger fires (handover needed)
        - Type-1: positions only (fast) if none of above
        """
        with self._lock:

            # ---------- Update config if provided ----------
            if config_payload:
                if "background_traffic_mbps" in config_payload:
                    #print(f"DEBUG: Payload contains: {config_payload['background_traffic_mbps']}")
                    new_bg = float(config_payload["background_traffic_mbps"])
                    self._state.background_traffic_mbps = new_bg
                    #print(f"DEBUG: Current load is {self._state.background_traffic_mbps}")
                if "background_traffic_injection_type" in config_payload:
                    new_type = str(config_payload["background_traffic_injection_type"])
                    self._state.background_traffic_injection_type = new_type
                if "buffer_size" in config_payload:
                    new_buffer = int(config_payload["buffer_size"])
                    self._state.buffer_size = new_buffer
                    # Propagate to active nodes
                    for node in self._network_orchestrator._nodes:
                        node.buffer_size_packets = new_buffer

            # ---------- Handle uninitialized case ----------                        
            if not self._state.initialized:
                now_ref = time.time() if reference_time is None else float(reference_time)
                self._state.ref_time_previous = self._state.ref_time_current
                self._state.ref_time_current = now_ref
                fallback = self._bridge._normalize_payload(self._bridge._fallback_positions(now_ref), now_ref)
                return {
                    "ok": True,
                    "call_type": "fallback",
                    "reference_time": now_ref,
                    "next_type2_after": 0.0,
                    "matlab": self._state.matlab_info,
                    "handover": {"count": 0, "gateway": None, "route_changed": False},
                    "handover_mode": self._state.handover_mode,
                    "snr_trigger": {"triggered": False, "reason": "simulation_not_initialized"},
                    "positions": fallback,
                    "route": None,
                }

            # Allow frontend to send a small integer offset (0,1,2...) relative
            # to the simulation start time. If a large/absolute timestamp is
            # provided, use it directly. If omitted, use current time.
            # ---------- Determine reference time ----------
            if reference_time is None:
                now_ref = time.time()
            else:
                now_ref = float(reference_time)
            self._state.ref_time_previous = self._state.ref_time_current
            self._state.ref_time_current = now_ref    

            # ---------- DECISION: Type-1 or Type-2? ----------
            should_call_type2 = (self._state.last_route is None) or (now_ref >= self._state.link_valid_until)

            # Evaluate SNR-based triggers (may force a Type 2 before expiry)
            snr_triggered = False
            snr_reason = None
            try:
                snr_triggered, snr_reason = self._evaluate_snr_trigger(now_ref)
            except Exception:
                snr_triggered, snr_reason = False, None

            if snr_triggered:
                # Respect cooldown so we don't spam Type-2 calls
                if now_ref - self._state.last_forced_type2_at > self._snr_cooldown_after_forced_s:
                    should_call_type2 = True
                    self._state.last_forced_type2_at = now_ref

            # ------------------------------------------------------------------
            # TYPE-2: Full recompute (positions + links + route)
            # ------------------------------------------------------------------

            if should_call_type2:
                previous_route_path = list(self._state.last_route.path) if self._state.last_route else []
                handover_detected = False

                # Call MATLAB for full link budget
                matlab_payload = self._bridge.type2_link_budget(now_ref)
                self._state.last_positions = matlab_payload
                self._state.last_links = list(matlab_payload.get("links", []))
                
                # Update snr history with fresh Type-2 link samples
                try:
                    self._update_snr_history(self._state.last_links, now_ref)
                except Exception:
                    pass
                self._state.last_type2_at = now_ref

                # Set new validity window
                valid_for_s = float(matlab_payload.get("link_valid_for_seconds", 20.0))
                self._state.link_valid_until = now_ref + max(2.0, valid_for_s)
                
                 # Recompute route
                route = None
                if self._state.source != self._state.destination:
                    route = self._network_orchestrator.compute_route()

                    # Check if route changed (handover)
                    if previous_route_path and route.success and route.path != previous_route_path:
                        self._state.handover_count += 1
                        handover_detected = True
                    self._state.last_route = route
                    call_type = "type2"
                    if route is not None:
                        self._network_orchestrator.refresh_active_route_link_properties()
                        self._network_orchestrator.run_simulation(handover_detected)
            
            # ------------------------------------------------------------------
            # TYPE-1: Positions only (fast)
            # ------------------------------------------------------------------
            
            else:
                # Call MATLAB for positions only
                matlab_payload = self._bridge.type1_positions(now_ref)
                # If Type-1 unexpectedly returns a very small satellite set while
                # MATLAB reports a larger configured constellation, attempt a
                # single Type-2 fallback to obtain the full positions and links.
                try:
                    expected_n = int(self._state.matlab_info.get("number_satellites", 0))
                except Exception:
                    expected_n = 0
                sat_count = len(matlab_payload.get("satellites", []) if matlab_payload else [])
                if expected_n and sat_count and expected_n > sat_count and self._bridge.available:
                    try:
                        fb = self._bridge.type2_link_budget(now_ref)
                        # prefer Type-2 payload if it appears reasonable
                        if fb and len(fb.get("satellites", [])) >= expected_n:
                            matlab_payload = fb
                            self._state.last_links = list(matlab_payload.get("links", []))
                    except Exception:
                        pass

                self._state.last_positions = matlab_payload
                
                # Update SNR history if available
                try:
                    self._update_snr_history(self._state.last_links, now_ref)
                except Exception:
                    pass

                # Reuse existing route
                route = self._state.last_route
                call_type = "type1"
                handover_detected = False
                if route is not None:
                    self._network_orchestrator.run_simulation()

            # ------------------------------------------------------------------
            # Return JSON response
            # ------------------------------------------------------------------

            return {
                "ok": True,
                "call_type": call_type,
                "reference_time": now_ref,
                "next_type2_after": self._state.link_valid_until,
                "matlab": self._state.matlab_info,
                "handover": {
                    "count": self._state.handover_count,
                    "gateway": None if route is None or not route.success or len(route.path) < 2 else route.path[1],
                    "route_changed": handover_detected,
                },
                "handover_mode": self._state.handover_mode,
                "snr_trigger": {"triggered": snr_triggered, "reason": snr_reason},
                "positions": {
                    "timestamp": self._state.last_positions.get("timestamp", now_ref),
                    "satellites": self._state.last_positions.get("satellites", []),
                    "links": self._state.last_positions.get("links", self._state.last_links),
                    "link_valid_for_seconds": self._state.last_positions.get("link_valid_for_seconds", max(0.0, self._state.link_valid_until - now_ref)),
                },
                "route": None
                if route is None
                else {
                    "source": route.source,
                    "destination": route.destination,
                    "path": route.path,
                    "hop_count": route.hop_count,
                    "total_cost": route.total_cost,
                    "metric": route.metric,
                    "success": route.success,
                    "error": route.error,
                },
                "user_measured_throughput": self._state.user_measured_throughput,
                "user_measured_latency": self._state.user_measured_latency,
                "user_measured_loss_ratio": self._state.user_measured_loss_ratio,          
                "packets_created": self._state.packets_created,
                "packets_arrived": self._state.packets_arrived,
                "route_node_stats": self._state.route_node_stats,
            }
