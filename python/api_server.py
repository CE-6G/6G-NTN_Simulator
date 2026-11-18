"""Flask API exposing the Python orchestration layer for UI and Cesium.

This module is the entry point for the entire simulator. It provides REST endpoints
that the browser frontend calls to initialize the simulation, advance time, and
retrieve updated positions, routes, and KPIs.

Key endpoints:
- /api/init: Loads TLE, sets up UEs, routing metric, traffic parameters
- /api/update: Advances simulation, decides Type-1/Type-2, returns state
- /api/health: Liveness check
- /api/tle_list: Lists available TLE files"""

from __future__ import annotations

import logging
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from flask import Flask, jsonify, request
from flask_cors import CORS
from pathlib import Path

from algorithms.dijkstra_router import CostMetric
from services.orchestrator import SimulationOrchestrator

# -------------------------------------------------------------------
# Application Setup
# -------------------------------------------------------------------

logging.basicConfig(level=logging.DEBUG)
warnings.filterwarnings("ignore")
logging.getLogger("werkzeug").setLevel(logging.DEBUG)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)
CORS(app) # Allow browser to call API from different origin

# Single orchestrator instance - holds all simulation state across requests
ORCHESTRATOR = SimulationOrchestrator()

# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------

def _parse_metric(value: str) -> CostMetric:
    """Convert string from frontend to CostMetric enum.
    Supported: min_hop, total_delay_ms, distance_km
    Default: min_hop if invalid."""

    normalized = (value or "").strip().lower()
    by_value: Dict[str, CostMetric] = {
        CostMetric.MIN_HOP.value: CostMetric.MIN_HOP,
        CostMetric.TOTAL_DELAY_MS.value: CostMetric.TOTAL_DELAY_MS,
        CostMetric.DISTANCE_KM.value: CostMetric.DISTANCE_KM,
    }
    return by_value.get(normalized, CostMetric.MIN_HOP)


def extract_tle_epoch(tle_filepath: str) -> float:

    """Extract simulation start time from TLE file's epoch field.
    Position 18-20: year, 20-32: day of year.
    Returns POSIX timestamp. Falls back to current time if parsing fails."""

    try:
        with open(tle_filepath, "r") as f:
            for line in f:
                if line.startswith("1 "):
                    epoch_year = int(line[18:20])
                    epoch_day = float(line[20:32])

                    year = (
                        2000 + epoch_year
                        if epoch_year < 57
                        else 1900 + epoch_year
                    )
                    dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
                        days=epoch_day - 1
                    )
                    
                    return dt.timestamp()
    except Exception:
        pass

    return time.time()

# -------------------------------------------------------------------
# Health & Status Endpoints
# -------------------------------------------------------------------

@app.get("/api/health")
def health() -> Any:
    """Liveness check - verifies backend is running."""
    return jsonify({"status": "ok", "timestamp": time.time()})


@app.get("/api/ready")
def ready() -> Any:
    """Reports whether simulation has been initialized."""
    return jsonify({"ready": ORCHESTRATOR.is_ready})

# -------------------------------------------------------------------
# /api/init - Simulation Setup
# -------------------------------------------------------------------

@app.post("/api/init")
def init_simulation() -> Any:
    """Initialize a new simulation scenario.

    Called once when user clicks 'Init' in frontend.
    Loads TLE, sets up UEs, routing metric, traffic parameters.
    Returns first state: positions, links, route, KPIs.
    Returns:
        JSON with positions, links, route, KPIs, and state metadata."""

    app.logger.info(
        "path=%s ip=%s",
        request.path,
        request.remote_addr,
    )

    start_time = time.time()
    payload = request.get_json(force=True)
    # ---------- Extract parameters from JSON ----------
    tle_file = str(payload.get("tle_file", ""))
    source = str(payload.get("source", "0"))
    destination = str(payload.get("destination", "1"))
    metric = _parse_metric(
        str(payload.get("metric", CostMetric.MIN_HOP.value))
    )
    handover_mode = str(payload.get("handover_mode", "margin"))
    ground_nodes = payload.get("ground_nodes", [])
    buffer_size = float(payload.get("buffer_size", 256))
    packet_size_bytes = float(payload.get("packet_size_bytes", 1500))
    monitor_interval = float(payload.get("monitor_interval", 1))
    priority_levels = payload.get("priority_levels", [])
    user_flow_priority = float(payload.get("user_flow_priority", 1))
    user_flow_cbr_vbr = str(payload.get("user_flow_cbr_vbr", "CBR"))
    user_flow_peak_rate = float(payload.get("user_flow_peak_rate", 10))
    user_flow_avg_rate = float(payload.get("user_flow_avg_rate", 10))
    user_flow_burstiness = float(payload.get("user_flow_burstiness", 0))
    background_traffic_mbps = float(payload.get("background_traffic_mbps", 0.0))
    background_traffic_injection_type = str(payload.get("background_traffic_injection_type", "source_side_only"))

    # ---------- Resolve TLE file path ----------
    # Handles absolute paths, relative paths, and wildcard search

    root = Path(__file__).resolve().parents[1]
    resolved_tle = None
    if tle_file:
        tpath = Path(tle_file)
        if tpath.is_absolute() and tpath.exists():
            resolved_tle = str(tpath)
        else:
            candidate = root / tle_file
            if candidate.exists():
                resolved_tle = str(candidate)
            else:
                try:
                    for p in root.rglob("*.tle"):
                        if p.name.lower() == tpath.name.lower():
                            resolved_tle = str(p)
                            break
                except Exception:
                    resolved_tle = None

    use_tle = resolved_tle or tle_file

    # ---------- Get simulation start time from TLE ----------
    simulation_start_time = extract_tle_epoch(use_tle)

    # ---------- Initialize orchestrator ----------
    # This calls MATLAB initialization, computes first route,
    # and initializes packet simulation.
    result = ORCHESTRATOR.initialize(
        tle_file=use_tle,
        simulation_start_time=simulation_start_time,
        source=source,
        destination=destination,
        metric=metric,
        handover_mode=handover_mode,
        ground_nodes=ground_nodes,
        buffer_size=buffer_size,
        packet_size_bytes=packet_size_bytes,
        monitor_interval=monitor_interval,
        priority_levels=priority_levels,
        user_flow_priority=user_flow_priority,
        user_flow_cbr_vbr=user_flow_cbr_vbr,
        user_flow_peak_rate=user_flow_peak_rate,
        user_flow_avg_rate=user_flow_avg_rate,
        user_flow_burstiness=user_flow_burstiness,
        background_traffic_mbps=background_traffic_mbps,
        background_traffic_injection_type=background_traffic_injection_type
    )

    end_time = time.time()
    elapsed_s = end_time - start_time
    app.logger.info(
        "initialization complete: elapsed=%.6f s",
        elapsed_s,
    )
    return jsonify(result)

# -------------------------------------------------------------------
# /api/update - Simulation Heartbeat
# -------------------------------------------------------------------
@app.post("/api/update")
def update_route() -> Any:
    """Advance simulation to a new time.
    Called repeatedly (every second via Auto Poll, or manually).
    This is where the Type-1/Type-2 decision happens.

    Returns:
        Updated positions, links, route, KPIs, handover state.

    The orchestrator decides:
        - Type-1: positions only (fast, reuses route)
        - Type-2: full link budget + route recompute (slow, on expiry or SNR trigger)
    """
    start_time = time.time()
    reference_time_raw = request.args.get("reference_time")
    reference_time = float(reference_time_raw) if reference_time_raw else None
    
    # Extract config from JSON body (e.g., background traffic slider)
    config_payload = request.get_json(silent=True) or {}

    app.logger.info(
        "path=%s ip=%s type 1 call (satellite position only) at reference_time=%s with config=%s",
        request.path,
        request.remote_addr,
        reference_time,
        config_payload,
    )

    # Core orchestration: Type-1/Type-2 decision, MATLAB calls, routing, packet sim
    result = ORCHESTRATOR.update(reference_time, config_payload=config_payload)

    end_time = time.time()
    elapsed_s = end_time - start_time
    app.logger.info(
        "update complete: elapsed=%.6f s",
        elapsed_s,
    )
    return jsonify(result), 200


# -------------------------------------------------------------------
# Utility Endpoints
# -------------------------------------------------------------------
@app.get("/api/tle_list")
def tle_list() -> Any:
    """List all .tle files in the project.
    Scans recursively from project root.
    Returns relative paths for frontend dropdown.
    """
    app.logger.info(
        "path=%s ip=%s",
        request.path,
        request.remote_addr,
    )

    root = Path(__file__).resolve().parents[1]
    tle_paths = []
    for p in root.rglob("*.tle"):
        try:
            tle_paths.append(str(p.relative_to(root)))
        except Exception:
            tle_paths.append(str(p))
    return jsonify({"tle_files": sorted(tle_paths)})


@app.get("/api/matlab_state")
def matlab_state() -> Any:
    """Debug endpoint - query MATLAB current state.

    Returns: satellite count, TLE file, sample time.
    Used for diagnostics and status display.
    """
    app.logger.info(
        "path=%s ip=%s",
        request.path,
        request.remote_addr,
    )

    try:
        bridge = ORCHESTRATOR._bridge
        if bridge.available and bridge.engine is not None:
            try:
                eng = bridge.engine
                n_sat = int(
                    eng.eval(
                        "numel(ntn_bridge_state('get').satellites)", nargout=1
                    )
                )
                tle = str(
                    eng.eval(
                        "string(ntn_bridge_state('get').tleFile)", nargout=1
                    )
                )
                sample = float(
                    eng.eval(
                        "double(ntn_bridge_state('get').sampleTime)", nargout=1
                    )
                )
                return jsonify(
                    {
                        'ok': True,
                        'number_satellites': n_sat,
                        'tle_file': tle,
                        'sample_time_seconds': sample,
                    }
                )
            except Exception as exc:
                return (
                    jsonify(
                        {
                            'ok': False,
                            'error': f'matlab_call_failed: {str(exc)}',
                        }
                    ),
                    500,
                )
        return jsonify({'ok': False, 'error': 'matlab_engine_unavailable'}), 404
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.post("/api/start_matlab")
def start_matlab() -> Any:
    """Attempt to connect to MATLAB Engine.
    If MATLAB is available, connects and returns success.
    If not, returns fallback mode (Python generates dummy constellation).
    Allows frontend to test without MATLAB.
    """
    app.logger.info(
        "path=%s ip=%s",
        request.path,
        request.remote_addr,
    )

    try:
        bridge = ORCHESTRATOR._bridge
        if not bridge.available:
            return (
                jsonify({'ok': False, 'message': 'matlab_engine_not_installed'}),
                503,
            )

        if bridge.engine is not None:
            return jsonify({'ok': True, 'message': 'matlab_already_connected'})

        try:
            connected = bridge.connect()
        except Exception as exc:
            print(f"DEBUG: connect() raised exception: {exc}")
            connected = False

        if connected and bridge.engine is not None:
            return jsonify({'ok': True, 'message': 'matlab_connected'})

        return (
            jsonify(
                {
                    'ok': True,
                    'message': 'matlab_available_fallback',
                    'note': 'Using Python simulation fallback',
                }
            ),
            200,
        )
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500

# -------------------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)