function P = ntn_positions_matrix(reference_time)
%NTN_POSITIONS_MATRIX Compute satellite ECEF position matrix at a given epoch.
%   P = NTN_POSITIONS_MATRIX(REFERENCE_TIME) queries the active persistent
%   simulation state and evaluates the Earth-Centered, Earth-Fixed (ECEF)
%   Cartesian coordinates [m] of every loaded satellite at REFERENCE_TIME
%   by calling STATES individually per spacecraft.
%
%   Syntax:
%       P = ntn_positions_matrix(reference_time)
%
%   Inputs:
%       reference_time - Query epoch specified as a numeric Unix POSIX
%                        timestamp [s] (seconds since 1970-01-01 UTC).
%
%   Outputs:
%       P              - N-by-3 double matrix of satellite ECEF coordinates
%                        [X, Y, Z] in meters [m], where N is the number of
%                        satellites in state.satellites. Rows whose state
%                        cannot be evaluated remain NaN. Returns a 0-by-3
%                        matrix if no satellites are loaded.
%
%   Notes:
%       Performance Warning: Calling states() individually inside a loop is
%       computationally expensive (~2 minutes for 75 satellites). Use this
%       function only as a fallback; prefer ntn_satellite_positions or
%       ntn_update_position for routine propagation queries.
%
%   See also NTN_BRIDGE_STATE, NTN_SATELLITE_POSITIONS, STATES, DATETIME.

    %% Retrieve Active Simulation State
    state = ntn_bridge_state("get");
    if ~isfield(state, "satellites") || isempty(state.satellites)
        P = zeros(0,3);
        return;
    end

    %% Timestamp Conversion and Output Preallocation
    t = datetime(double(reference_time), "ConvertFrom", "posixtime", "TimeZone", "UTC");
    n = numel(state.satellites);
    P = NaN(n,3);

    %% Per-Satellite ECEF State Evaluation
    for ii = 1:n
        % Query satellite position in the Earth-Centered, Earth-Fixed frame
        p = squeeze(states(state.satellites(ii), t, "CoordinateFrame", "ecef"));
        if isempty(p)
            continue;
        end

        % Normalize column vector [3 x 1] output to row vector [1 x 3]
        if size(p,1) == 3 && size(p,2) ~= 3
            p = p.';
        end

        P(ii,:) = double(p(1,:));
    end
end