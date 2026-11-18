function [satellites, satPosMeters] = ntn_satellite_positions(state, reference_time)
%NTN_SATELLITE_POSITIONS Evaluate current satellite positions in the ECEF frame.
%   [SATELLITES, SATPOSMETERS] = NTN_SATELLITE_POSITIONS(STATE, REFERENCE_TIME)
%   queries the Earth-Centered, Earth-Fixed (ECEF) Cartesian positions of all
%   satellites stored in STATE at epoch REFERENCE_TIME. It first attempts a
%   vectorized STATES call across the constellation and automatically falls
%   back to per-satellite queries if the vectorized output is incomplete or
%   unsupported by the installed Aerospace Toolbox version.
%
%   Syntax:
%       [satellites, satPosMeters] = ntn_satellite_positions(state, reference_time)
%
%   Inputs:
%       state          - Simulation state struct (from ntn_bridge_state)
%                        containing field:
%                          * satellites - Array of Satellite objects.
%       reference_time - Query epoch specified either as a MATLAB datetime
%                        scalar or a numeric Unix POSIX timestamp [s].
%
%   Outputs:
%       satellites     - N-by-1 struct array of satellite coordinates in
%                        kilometers, with 0-indexed string IDs:
%                          * id   - Zero-indexed satellite ID string ("0", "1", ...).
%                          * x_km - ECEF X coordinate [km].
%                          * y_km - ECEF Y coordinate [km].
%                          * z_km - ECEF Z coordinate [km].
%       satPosMeters   - N-by-3 double matrix of satellite ECEF coordinates
%                        [X, Y, Z] in meters [m]. Entries that fail evaluation
%                        are populated with NaN.
%
%   Notes:
%       If state.satellites is missing or empty, returns an empty 0-by-0
%       struct array and a 0-by-3 double matrix.
%
%   See also NTN_BRIDGE_STATE, NTN_POSITIONS_MATRIX, STATES, DATETIME.

    %% Early Exit for Empty Satellite Constellation
    if ~isfield(state, "satellites") || isempty(state.satellites)
        satellites = struct([]);
        satPosMeters = zeros(0, 3);
        return;
    end

    %% Vectorized ECEF State Query
    queryTime = to_datetime(reference_time);
    rawPos = [];
    try
        rawPos = squeeze(states(state.satellites, queryTime, "CoordinateFrame", "ecef"));
    catch
        rawPos = [];
    end

    %% Validate Vectorized Dimensions and Determine Fallback Mode
    % Verify that the vectorized call returned a complete N-by-3 matrix;
    % otherwise fall back to individual per-satellite states() calls.
    satCount = numel(state.satellites);
    use_per_sat = false;
    if isempty(rawPos)
        use_per_sat = true;
    else
        % Normalize 3-by-N output orientation to N-by-3
        if ismatrix(rawPos)
            if size(rawPos,1) == 3 && size(rawPos,2) ~= 3
                rawPos = rawPos.'; % Transpose to N-by-3
            end
            % Trigger fallback if row count does not match constellation size
            if size(rawPos,1) ~= satCount
                use_per_sat = true;
            end
        else
            use_per_sat = true;
        end
    end

    %% Extract Position Matrix (Fallback Loop or Vectorized Result)
    if use_per_sat
        satPosMeters = zeros(satCount, 3);
        for idx = 1:satCount
            try
                singlePos = squeeze(states(state.satellites(idx), queryTime, "CoordinateFrame", "ecef"));
            catch
                singlePos = [];
            end
            if isempty(singlePos)
                satPosMeters(idx, :) = [NaN, NaN, NaN];
            else
                if size(singlePos,1) == 3 && size(singlePos,2) ~= 3
                    singlePos = singlePos.';
                end
                satPosMeters(idx, :) = double(singlePos(1, :));
            end
        end
    else
        satPosMeters = double(rawPos);
    end

    %% Convert Units and Build Output Struct Array
    satPosKm = satPosMeters / 1000.0; % Convert ECEF coordinates from [m] to [km]
    nSat = size(satPosKm, 1);

    template = struct("id", "", "x_km", 0, "y_km", 0, "z_km", 0);
    satellites = repmat(template, nSat, 1);
    for idx = 1:nSat
        satellites(idx).id = string(idx - 1); % Assign 0-indexed string identifier
        satellites(idx).x_km = satPosKm(idx, 1);
        satellites(idx).y_km = satPosKm(idx, 2);
        satellites(idx).z_km = satPosKm(idx, 3);
    end
end

%% Local Helper Functions

function dt = to_datetime(reference_time)
%TO_DATETIME Convert a POSIX timestamp or datetime input into a UTC datetime.
%   DT = TO_DATETIME(REFERENCE_TIME) normalizes the input epoch to a
%   timezone-aware UTC datetime scalar.
    if isa(reference_time, "datetime")
        dt = reference_time;
        if isempty(dt.TimeZone)
            dt.TimeZone = "UTC";
        end
        return;
    end

    dt = datetime(double(reference_time), "ConvertFrom", "posixtime", "TimeZone", "UTC");
end