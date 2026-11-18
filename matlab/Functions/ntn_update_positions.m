function result = ntn_update_positions(reference_time)
%NTN_UPDATE_POSITIONS Retrieve satellite ECEF positions formatted for Python engine transfer.
%   RESULT = NTN_UPDATE_POSITIONS(REFERENCE_TIME) queries the active persistent
%   simulation state, evaluates the Earth-Centered, Earth-Fixed (ECEF)
%   coordinates of all loaded satellites at REFERENCE_TIME, and packs the
%   results into flat column arrays inside a scalar struct suitable for
%   automatic serialization across the MATLAB Engine API for Python.
%
%   Syntax:
%       result = ntn_update_positions(reference_time)
%
%   Inputs:
%       reference_time - Query epoch specified as a MATLAB datetime scalar or
%                        a numeric Unix POSIX timestamp [s].
%
%   Outputs:
%       result         - Scalar struct containing flattened constellation state:
%                          * timestamp     - Query timestamp as double [s].
%                          * satellites_id - N-by-1 cell array of satellite ID
%                                            strings ("0", "1", ...).
%                          * satellites_x  - N-by-1 double vector of ECEF X
%                                            positions [km].
%                          * satellites_y  - N-by-1 double vector of ECEF Y
%                                            positions [km].
%                          * satellites_z  - N-by-1 double vector of ECEF Z
%                                            positions [km].
%
%   Notes:
%       Requires prior initialization via ntn_initialize_simulation. Throws
%       'ntn_update_positions:not_initialized' if the persistent bridge state
%       has not been initialized.
%
%   See also NTN_BRIDGE_STATE, NTN_INITIALIZE_SIMULATION, NTN_SATELLITE_POSITIONS.

    %% Validate Persistent Simulation State
    state = ntn_bridge_state("get");
    if ~isfield(state, "initialized") || ~state.initialized
        error("ntn_update_positions:not_initialized", "Simulation has not been initialized");
    end

    %% Query Satellite Positions
    [satellites, ~] = ntn_satellite_positions(state, reference_time);

    %% Flatten Struct Array into Engine-Compatible Column Vectors
    nSat = numel(satellites);
    sat_ids = cell(nSat, 1);
    sat_x = zeros(nSat, 1);
    sat_y = zeros(nSat, 1);
    sat_z = zeros(nSat, 1);
    
    for idx = 1:nSat
        sat_ids{idx} = satellites(idx).id;
        sat_x(idx) = satellites(idx).x_km;
        sat_y(idx) = satellites(idx).y_km;
        sat_z(idx) = satellites(idx).z_km;
    end
    
    %% Package Output Structure
    result = struct( ...
        "timestamp", double(reference_time), ...
        "satellites_id", {sat_ids}, ...
        "satellites_x", sat_x, ...
        "satellites_y", sat_y, ...
        "satellites_z", sat_z);
end