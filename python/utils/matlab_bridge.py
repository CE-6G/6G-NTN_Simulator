"""Bridge layer for MATLAB type-1 and type-2 simulation calls.

Type 1: update satellite positions only.
Type 2: update positions + link budget and estimated validity duration.

This module handles all MATLAB communication and data conversion.
If MATLAB fails, it provides a deterministic fallback constellation.
"""

from __future__ import annotations
import json
import io
import math
import sys
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.topology import TopologyBuilder

import logging
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=UserWarning, module="matlab")

@dataclass
class MatlabBridge:
    """MATLAB engine wrapper with robust fallbacks."""

    engine: Any = None
    available: bool = False

    def __post_init__(self) -> None:
        """Initialize bridge, detect MATLAB Engine availability."""
        self._topology = TopologyBuilder()
        self._supported_python = (3, 9) <= sys.version_info[:2] <= (3, 13)
        self._matlab_engine = None
        self._tle_file: Optional[str] = None
        self._fallback_satellite_count = 75

        # Check if MATLAB Engine for Python is installed
        if not self._supported_python:
            self.available = False
            return

        try:
            import matlab.engine  # type: ignore

            self._matlab_engine = matlab.engine
            self.available = True
        except Exception:
            self._matlab_engine = None
            self.available = False

    # ------------------------------------------------------------------
    # MATLAB Connection
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Attach to an existing MATLAB session or start a new one."""
        if not self.available or self._matlab_engine is None:
            return False

        if self.engine is not None:
            return True

        try:
            # Try to connect to existing MATLAB session first
            sessions = self._matlab_engine.find_matlab()
            if sessions:
                self.engine = self._matlab_engine.connect_matlab(sessions[0])
            else:
                # Start new MATLAB engine
                self.engine = self._matlab_engine.start_matlab()
            # Add project paths so MATLAB can find our functions
            self._add_project_paths()
            #self.engine.eval("dbstop in ntn_link_budget at 46", nargout=0) # insert breakpoint in matlab script at chosen line
            return self.engine is not None
        except Exception:
            self.engine = None
            return False

    def _add_project_paths(self) -> None:
        """Make MATLAB bridge functions discoverable by the engine."""
        if self.engine is None:
            return

        matlab_root = Path(__file__).resolve().parents[2] / "matlab"
        functions_path = matlab_root / "Functions"
        try:
            if matlab_root.exists():
                self.engine.addpath(str(matlab_root), nargout=0)
            if functions_path.exists():
                self.engine.addpath(str(functions_path), nargout=0)
        except Exception:
            pass

    def _count_satellites_from_tle(self, tle_file: str) -> int:
        """Count satellites in TLE file by counting lines starting with '1 '."""
        try:
            p = Path(tle_file)
            if not p.exists():
                return 0
            lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
            line1_count = sum(1 for ln in lines if ln.startswith("1 "))
            line2_count = sum(1 for ln in lines if ln.startswith("2 "))
            return min(line1_count, line2_count)
        except Exception:
            return 0


    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize_simulation(self, tle_file: str, simulation_start_time: float, user_equipments: dict) -> Dict[str, Any]:
        """Send initial simulation setup to MATLAB.
        Calls MATLAB's ntn_initialize_simulation with TLE file and UEs.
        Falls back to Python if MATLAB unavailable.
        """
        self._tle_file = str(tle_file)
        tle_count = self._count_satellites_from_tle(str(tle_file))
        if tle_count > 0:
            self._fallback_satellite_count = max(75, tle_count)

        if self.available and self.engine is not None:
            try:
                out_buffer = io.StringIO()
                err_buffer = io.StringIO()

                with self._suppress_stderr():
                    result = self.engine.ntn_initialize_simulation(
                        str(tle_file), 
                        float(simulation_start_time), 
                        json.dumps(user_equipments), 
                        nargout=1,
                        stdout=out_buffer,
                        stderr=err_buffer
                    )
                
                # Print MATLAB timing logs for debugging
                print("\n=== MATLAB TIMING LOGS ===")
                print(out_buffer.getvalue())
                print("==========================\n")

                result_dict = dict(result)
                try:
                    n = int(result_dict.get("number_satellites", 0))
                    if n > 0:
                        self._fallback_satellite_count = max(75, n)
                except Exception:
                    pass
                logger.debug("MATLAB initialized correctly!")
                return result_dict
            except Exception as exc:
                logger.warning("MATLAB bridge is present, but an error occurred.")
                return {"ok": False, "bridge": "matlab_engine", "error": str(exc)}

        if not self._supported_python:
            logger.warning("Problems in MATLAB initialization.")
            return {
                "ok": False,
                "bridge": "python_fallback",
                "error": f"MATLAB Engine for Python is unsupported on Python {sys.version_info[0]}.{sys.version_info[1]}; use Python 3.9-3.13",
            }
        # Fallback: Python generates dummy constellation
        logger.warning("Problems in MATLAB initialization. Using python fallback instead.")
        return {
            "ok": True,
            "bridge": "python_fallback",
            "number_satellites": float(self._fallback_satellite_count),
            "tle_file": self._tle_file or "",
        }


    # ------------------------------------------------------------------
    # Helpers & Fallback
    # ------------------------------------------------------------------

    def _matlab_positions_from_state(self, reference_time: float) -> List[Dict[str, Any]]:
        """Extract positions from MATLAB persistent state (slow fallback)."""
        if not (self.available and self.engine is not None):
            return []

        try:
            with self._suppress_stderr():
                n_sat = int(self.engine.eval("numel(ntn_bridge_state('get').satellites)", nargout=1))
        except Exception:
            return []

        if n_sat <= 0 or n_sat > 5000:
            return []

        try:
            with self._suppress_stderr():
                matpos = self.engine.ntn_positions_matrix(float(reference_time), nargout=1) # takes 2+ minutes to execute!
        except Exception:
            return []

        satellites: List[Dict[str, Any]] = []
        try:
            for i_idx in range(len(matpos)):
                row = matpos[i_idx]
                x = float(row[0])
                y = float(row[1])
                z = float(row[2])
                if any(math.isnan(v) for v in (x, y, z)):
                    continue
                satellites.append(
                    {
                        "id": str(i_idx),
                        "x_km": x / 1000.0,
                        "y_km": y / 1000.0,
                        "z_km": z / 1000.0,
                    }
                )
        except Exception:
            return []

        return satellites

    def _payload_from_satellites(self, satellites: List[Dict[str, Any]], reference_time: float) -> Dict[str, Any]:
        """Build minimal payload from satellite positions only."""
        payload = {
            "timestamp": float(reference_time),
            "generated_at": time.time(),
            "satellites": satellites,
        }
        return self._normalize_payload(payload, reference_time)

# ------------------------------------------------------------------
    # Data Conversion (MATLAB → Python)
# ------------------------------------------------------------------

    def _to_list(self, value):
        """Convert MATLAB array to Python list, handling nested structures."""
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            return [value]
        try:
            items = list(value)
        except TypeError:
            return [value]
        # MATLAB often returns [[1,2,3]] - flatten it
        if len(items) == 1 and isinstance(items[0], (list, tuple)):
            return list(items[0])
        return items

    def _to_scalar(self, value):
        """Extract a single float from MATLAB nested array."""
        if isinstance(value, (int, float)):
            return float(value)

        try:
            items = list(value)
        except TypeError:
            return float(value)
        # Unwrap nested arrays until we get a number
        while isinstance(items, list) and len(items) == 1:
            value = items[0]
            if isinstance(value, (int, float)):
                return float(value)
            try:
                items = list(value)
            except TypeError:
                return float(value)

        if isinstance(items, list) and len(items) > 0:
            first = items[0]
            if isinstance(first, (list, tuple)) and len(first) > 0:
                return float(first[0])
            return float(first)

        raise ValueError(f"Cannot convert to scalar float: {value!r}")

    def _to_float_list(self, value):
        """Convert MATLAB array to Python list of floats."""
        return [self._to_scalar(x) for x in self._to_list(value)]

    def _to_str_list(self, value):
        """Convert MATLAB array to Python list of strings."""
        out = []
        for x in self._to_list(value):
            try:
                items = list(x)
                if len(items) == 1:
                    out.append(str(items[0]))
                else:
                    out.append(str(x))
            except TypeError:
                out.append(str(x))
        return out

    @contextmanager
    def _suppress_stderr(self):
        """Context manager to suppress stderr and warnings from MATLAB engine calls.
        
        Uses Python-level redirection which works reliably on all platforms including Windows.
        """
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                yield
            finally:
                sys.stderr = old_stderr

    # ------------------------------------------------------------------
    # Type-1: Positions Only (Fast)
    # ------------------------------------------------------------------

    def type1_positions(self, reference_time: float) -> Dict[str, Any]:
        """Type-1 call: update satellite positions only.
        Calls MATLAB ntn_update_positions.
        Fast - no link budget computation.
        """
        logger.debug("Executing type 1 call (position only)")

        if self.available and self.engine is not None:
            matlab_error = ""

            try:
                with self._suppress_stderr():
                    result = self.engine.ntn_update_positions(float(reference_time), nargout=1)

                raw = dict(result)

                sat_ids = self._to_str_list(raw.get("satellites_id"))
                sat_x = self._to_float_list(raw.get("satellites_x"))
                sat_y = self._to_float_list(raw.get("satellites_y"))
                sat_z = self._to_float_list(raw.get("satellites_z"))
                
                # Build satellites list
                satellites = []
                n_sat = min(len(sat_ids), len(sat_x), len(sat_y), len(sat_z))
                for i in range(n_sat):
                    satellites.append(
                        {
                            "id": sat_ids[i],
                            "x_km": sat_x[i],
                            "y_km": sat_y[i],
                            "z_km": sat_z[i],
                        }
                    )

                payload = {
                    "timestamp": float(raw.get("timestamp", reference_time)),
                    "generated_at": time.time(),
                    "satellites": satellites,
                }
                payload["satellites"] = self._topology.normalize_satellites(payload.get("satellites", []))

                # If we got enough satellites, return
                if len(payload.get("satellites", [])) >= 12:
                    return payload

                matlab_error = "truncated_type1_payload"

            except Exception as exc:
                matlab_error = str(exc)
                logger.error("Error while executing type 1 call: %s", matlab_error)

            # Recovery: try to get positions from MATLAB state
            satellites = self._matlab_positions_from_state(reference_time)
            if satellites:
                payload = self._payload_from_satellites(satellites, reference_time)
                payload.setdefault("_diagnostics", {})["bridge"] = "matlab_state_recovery"
                payload.setdefault("_diagnostics", {})["matlab_error"] = matlab_error
                return payload
            
            # Ultimate fallback: Python constellation
            payload = self._normalize_payload(self._fallback_positions(reference_time), reference_time)
            payload.setdefault("_diagnostics", {})["bridge"] = "python_fallback"
            payload.setdefault("_diagnostics", {})["matlab_error"] = matlab_error
            return payload

        return self._normalize_payload(self._fallback_positions(reference_time), reference_time)


    # ------------------------------------------------------------------
    # Type-2: Full Link Budget (Slow)
    # ------------------------------------------------------------------

    def type2_link_budget(self, reference_time: float) -> Dict[str, Any]:
        """Type-2 call: positions + link budget.
        Calls MATLAB ntn_link_budget.
        Returns positions, links, and validity window.
        """
        logger.debug('Executing type 2 call (position + link budget)')
        if self.available and self.engine is not None:
            try:
                with self._suppress_stderr():
                    result = self.engine.ntn_link_budget(float(reference_time), nargout=1)

                raw = dict(result)
                # ---------- Convert satellite data ----------
                sat_ids = self._to_str_list(raw.get("satellites_id"))
                link_source = self._to_str_list(raw.get("links_source"))
                link_target = self._to_str_list(raw.get("links_target"))
                sat_x = self._to_float_list(raw.get("satellites_x"))
                sat_y = self._to_float_list(raw.get("satellites_y"))
                sat_z = self._to_float_list(raw.get("satellites_z"))

                satellites = []
                n_sat = min(len(sat_ids), len(sat_x), len(sat_y), len(sat_z))
                for i in range(n_sat):
                    satellites.append(
                        {
                            "id": sat_ids[i],
                            "x_km": sat_x[i],
                            "y_km": sat_y[i],
                            "z_km": sat_z[i],
                        }
                    )
                 
                # ---------- Convert link data ----------
                link_delay_ms = self._to_float_list(raw.get("links_delay_ms"))
                link_distance_km = self._to_float_list(raw.get("links_distance_km"))
                link_capacity_mbps = self._to_float_list(raw.get("links_capacity_mbps"))
                link_snr_db = self._to_float_list(raw.get("links_snr_db"))
                link_margin_db = self._to_float_list(raw.get("links_margin_db"))
                link_elevation_deg = self._to_float_list(raw.get("links_elevation_deg"))
                link_path_loss_db = self._to_float_list(raw.get("links_path_loss_db"))
                link_packet_loss_prob = self._to_float_list(raw.get("links_packet_loss_prob"))

                links = []
                n_links = min(
                    len(link_source),
                    len(link_target),
                    len(link_delay_ms),
                    len(link_distance_km),
                    len(link_capacity_mbps),
                    len(link_snr_db),
                    len(link_margin_db),
                    len(link_elevation_deg),
                    len(link_path_loss_db),
                    len(link_packet_loss_prob),
                )
                for i in range(n_links):
                    links.append(
                        {
                            "source": link_source[i],
                            "target": link_target[i],
                            "delay_ms": link_delay_ms[i],
                            "distance_km": link_distance_km[i],
                            "capacity_mbps": link_capacity_mbps[i],
                            "snr_db": link_snr_db[i],
                            "margin_db": link_margin_db[i],
                            "elevation_deg": link_elevation_deg[i],
                            "path_loss_db": link_path_loss_db[i],
                            "packet_loss_prob": link_packet_loss_prob[i],
                        }
                    )

                # Get validity window from MATLAB, or estimate it
                link_valid_for_seconds = raw.get("link_valid_for_seconds")
                if link_valid_for_seconds is None:
                    link_valid_for_seconds = self._topology.estimate_link_validity_seconds(satellites, links)

                payload = {
                    "timestamp": float(raw.get("timestamp", reference_time)),
                    "generated_at": time.time(),
                    "satellites": satellites,
                    "links": links,
                    "link_valid_for_seconds": float(link_valid_for_seconds),
                }

                payload = self._normalize_payload(payload, reference_time)
                if len(payload.get("satellites", [])) >= 12:
                    return payload
                
                # Recovery: if MATLAB returned truncated data, try fallback
                satellites = self._matlab_positions_from_state(reference_time)
                if satellites:
                    payload["satellites"] = satellites
                    payload["links"] = self._topology.normalize_links(
                        self._topology.build_links_from_positions(satellites)
                    )
                    return self._normalize_payload(payload, reference_time)

            except Exception as exc:
                matlab_error = str(exc)
                logger.error('Error while executing type 2 call: ', matlab_error)

                # Recovery attempt
                satellites = self._matlab_positions_from_state(reference_time)
                if satellites:
                    payload = self._payload_from_satellites(satellites, reference_time)
                    payload.setdefault("_diagnostics", {})["bridge"] = "matlab_state_recovery"
                    payload.setdefault("_diagnostics", {})["matlab_error"] = matlab_error
                    return payload

                payload = self._normalize_payload(self._fallback_positions(reference_time), reference_time)
                payload.setdefault("_diagnostics", {})["bridge"] = "python_fallback"
                payload.setdefault("_diagnostics", {})["matlab_error"] = matlab_error
                return payload

        return self._normalize_payload(self._fallback_positions(reference_time), reference_time)

    def _normalize_payload(self, payload: Dict[str, Any], reference_time: float) -> Dict[str, Any]:
        """Fill missing fields so Python always has a usable graph.
        - Ensures satellites are normalized
        - Builds links from positions if missing
        - Estimates validity window if missing
        """
        normalized = dict(payload)
        normalized.setdefault("timestamp", float(reference_time))
        normalized.setdefault("generated_at", time.time())

        satellites = self._topology.normalize_satellites(normalized.get("satellites", []))
        normalized["satellites"] = satellites

        links = normalized.get("links") or self._topology.build_links_from_positions(satellites)
        links = self._topology.normalize_links(links)
        normalized["links"] = links

        if "link_valid_for_seconds" not in normalized:
            normalized["link_valid_for_seconds"] = self._topology.estimate_link_validity_seconds(satellites, links)

        return normalized

    def _fallback_positions(self, reference_time: float) -> Dict[str, Any]:
        """Generate deterministic 75-satellite Iridium-like constellation.
        Used when MATLAB is unavailable.
        6 planes, ~12-13 satellites per plane at 780km altitude.
        """

        logger.warning('Fallback to deterministic 75-satellite Iridium-like constellation')
        satellites: List[Dict[str, Any]] = []

        n_total = max(75, int(self._fallback_satellite_count or 75))
        n_planes = 6
        base = n_total // n_planes
        rem = n_total % n_planes
        sats_per_plane = [base + (1 if i < rem else 0) for i in range(n_planes)]

        earth_radius_km = 6378.0
        orbit_alt_km = 780.0
        radius_km = earth_radius_km + orbit_alt_km
        inclination_rad = math.radians(86.4)
        earth_rot_rate = 7.2921159e-5
        t = float(reference_time)

        sat_id = 0
        for plane_idx, plane_count in enumerate(sats_per_plane):
            raan = (2.0 * math.pi * plane_idx) / n_planes
            phase_offset = (plane_idx % 2) * (math.pi / max(1, plane_count))

            for k in range(plane_count):
                mean_anomaly = (2.0 * math.pi * k / max(1, plane_count)) + phase_offset + (0.0009 * t)

                # Orbital to ECI
                x_orb = radius_km * math.cos(mean_anomaly)
                y_orb = radius_km * math.sin(mean_anomaly)

                x_inc = x_orb
                y_inc = y_orb * math.cos(inclination_rad)
                z_inc = y_orb * math.sin(inclination_rad)

                x_eci = x_inc * math.cos(raan) - y_inc * math.sin(raan)
                y_eci = x_inc * math.sin(raan) + y_inc * math.cos(raan)
                z_eci = z_inc

                # ECI to ECEF (Earth rotation)
                theta = earth_rot_rate * t
                x_ecef = x_eci * math.cos(theta) + y_eci * math.sin(theta)
                y_ecef = -x_eci * math.sin(theta) + y_eci * math.cos(theta)
                z_ecef = z_eci

                satellites.append(
                    {
                        "id": str(sat_id),
                        "x_km": x_ecef,
                        "y_km": y_ecef,
                        "z_km": z_ecef,
                    }
                )
                sat_id += 1

        return {
            "timestamp": float(reference_time),
            "generated_at": time.time(),
            "satellites": satellites,
        }
