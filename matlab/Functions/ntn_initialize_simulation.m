function result = ntn_initialize_simulation(tle_file, simulation_start_time, ground_nodes)
%NTN_INITIALIZE_SIMULATION Initialize a 6-hour NTN satellite scenario and bridge state.
%   RESULT = NTN_INITIALIZE_SIMULATION(TLE_FILE, SIMULATION_START_TIME) resets
%   the persistent simulation bridge state, spawns a 6-hour satelliteScenario
%   with a 30-second sample interval, filters decayed or malformed Two-Line
%   Element (TLE) entries, loads the satellite constellation, instantiates
%   default ground stations, and commits the active scenario handles to
%   persistent memory.
%
%   RESULT = NTN_INITIALIZE_SIMULATION(TLE_FILE, SIMULATION_START_TIME, GROUND_NODES)
%   populates the scenario with custom ground stations specified in GROUND_NODES.
%
%   Syntax:
%       result = ntn_initialize_simulation(tle_file, simulation_start_time)
%       result = ntn_initialize_simulation(tle_file, simulation_start_time, ground_nodes)
%
%   Inputs:
%       tle_file              - File path or filename (char/string) of the
%                               input TLE catalog. Resolved relative to the
%                               current working directory or MATLAB project
%                               directories if not directly found.
%       simulation_start_time - Scenario epoch specified either as a MATLAB
%                               datetime scalar or a numeric Unix POSIX
%                               timestamp (seconds since 1970-01-01 UTC).
%       ground_nodes          - (Optional) Struct array or JSON-encoded
%                               string defining ground stations passed to
%                               addGroundStations. Defaults to [] (spawns
%                               two baseline UE ground stations).
%
%   Outputs:
%       result                - Scalar struct summarizing initialization status:
%                                 * ok                     - Logical true on success.
%                                 * message                - Status message string.
%                                 * tle_file               - Path to resolved/cleaned TLE file.
%                                 * number_satellites      - Count of loaded satellites.
%                                 * number_ground_stations - Count of loaded ground stations.
%                                 * start_time             - Formatted scenario start timestamp.
%                                 * sample_time_seconds    - Scenario sample step [s] (30).
%
%   Notes:
%       Any pre-existing simulation state stored in ntn_bridge_state is reset
%       at the start of execution to prevent cross-run object collisions.
%       Satellites with mean motion >= 15.7 rev/day are pruned prior to
%       propagation to avoid SGP4 decay singularities.
%
%   See also SATELLITESCENARIO, ADDSATELLITES, ADDGROUNDSTATIONS, NTN_BRIDGE_STATE.

    %% Input Validation and State Reset
    fprintf('MATLAB: Starting simulation initialization pipeline...\n');
    if nargin < 1 || isempty(tle_file)
        error("ntn_initialize_simulation:tle_file", "tle_file is required");
    end
    if nargin < 2 || isempty(simulation_start_time)
        error("ntn_initialize_simulation:simulation_start_time", "simulation_start_time is required");
    end
    if nargin < 3
        ground_nodes = [];
    end

    % Reset persistent bridge state to prevent stale scenario handles across runs
    state = ntn_bridge_state("reset");

    % Configure 6-hour simulation window with 30-second propagation step
    startTime = to_datetime(simulation_start_time);
    stopTime = startTime + hours(6);
    sampleTime = 30; % Simulation sample interval [s]
    
    %% Scenario Creation and Asset Loading
    % Instantiate the empty satelliteScenario engine
    tic;
    sc = satelliteScenario(startTime, stopTime, sampleTime);
    fprintf('  -> Empty Satellite Scenario engine spawned in %.4f seconds.\n', toc);

    % Locate the TLE file and prune decayed or corrupted three-line entries
    tic;
    resolvedTle = resolve_tle_file(tle_file);
    cleanTleFile = filter_decayed_tle(resolvedTle);
    fprintf('  -> TLE track filtering and path resolution took %.4f seconds.\n', toc);

    % Propagate and attach satellite constellation objects
    tic;
    SVs = addSatellites(sc, cleanTleFile);
    fprintf('  -> Loaded %d tracking nodes into scenario memory space in %.4f seconds.\n', numel(SVs), toc);

    % Instantiate ground station nodes (custom definitions or default UE pair)
    tic;
    if ~isempty(ground_nodes)
        GS = addGroundStations(sc, ground_nodes);
    else
        GS = addGroundStations(sc);
    end
    fprintf('  -> Instantiated %d ground station objects in %.4f seconds.\n', numel(GS), toc);

    %% Commit Scenario State to Persistent Storage
    state.initialized = true;
    state.tleFile = string(cleanTleFile);
    state.simulationStartTime = startTime;
    state.startTime = startTime;
    state.stopTime = stopTime;
    state.sampleTime = sampleTime;
    state.scenario = sc;
    state.satellites = SVs;
    state.groundStations = GS;
    ntn_bridge_state("set", state);
    fprintf('MATLAB: Persistent initialization state committed successfully.\n\n');
    
    %% Package Output Summary Structure
    result = struct( ...
        "ok", true, ...
        "message", "simulation initialized", ...
        "tle_file", string(cleanTleFile), ...
        "number_satellites", numel(SVs), ...
        "number_ground_stations", numel(GS), ...
        "start_time", char(startTime), ...
        "sample_time_seconds", sampleTime);
end

%% Local Helper Functions

function cleanPath = filter_decayed_tle(originalTlePath)
%FILTER_DECAYED_TLE Remove decayed or malformed satellite records from a TLE file.
%   CLEANPATH = FILTER_DECAYED_TLE(ORIGINALTLEPATH) parses three-line element
%   sets (name, line 1, line 2), verifies line prefixes, and filters out
%   re-entering/decommissioned satellites whose mean motion is >= 15.7 rev/day.
%   If invalid entries are removed, a sanitized copy prefixed with 'clean_'
%   is written to disk and its path is returned; otherwise ORIGINALTLEPATH
%   is returned unchanged.
    fid = fopen(originalTlePath, 'r');
    if fid == -1
        error("Unable to open TLE file for filtering.");
    end
    
    content = textscan(fid, '%s', 'Delimiter', '\n');
    fclose(fid);
    lines = content{1};
    
    cleanLines = {};
    idx = 1;
    uncleanFile = 0;

    % Iterate through 3-line TLE blocks (Line 0: Name, Line 1: Epoch/Drag, Line 2: Orbit)
    while idx <= length(lines) - 2
        line0 = lines{idx};
        line1 = lines{idx+1};
        line2 = lines{idx+2};
        
        if startsWith(line1, '1 ') && startsWith(line2, '2 ')
            % Extract mean motion [rev/day] from columns 53-63 of TLE Line 2
            meanMotionStr = line2(53:63);
            meanMotion = str2double(meanMotionStr);
            
            % Retain active orbital objects below the ~15.7 rev/day decay threshold
            if ~isnan(meanMotion) && meanMotion < 15.7
                cleanLines{end+1} = line0;
                cleanLines{end+1} = line1;
                cleanLines{end+1} = line2;
            else
                uncleanFile = 1;
            end
            idx = idx + 3;
        else
            % Advance by one line to resynchronize if header/corrupt lines appear
            idx = idx + 1;
        end
    end
    
    % Write sanitized TLE file only when decayed or corrupt blocks were excluded
    if uncleanFile == 1
        [dirPath, fileName, ~] = fileparts(originalTlePath);
        cleanPath = fullfile(dirPath, ['clean_' char(fileName) '.tle']);
        fidOut = fopen(cleanPath, 'w');
        for i = 1:length(cleanLines)
            fprintf(fidOut, '%s\n', cleanLines{i});
        end
        fclose(fidOut);
    else
        cleanPath = originalTlePath;
    end
end

function dt = to_datetime(simulation_start_time)
%TO_DATETIME Convert a POSIX timestamp or datetime input into a UTC datetime.
%   DT = TO_DATETIME(SIMULATION_START_TIME) ensures the simulation epoch is
%   returned as a timezone-aware UTC datetime scalar.
    if isa(simulation_start_time, "datetime")
        dt = simulation_start_time;
        if isempty(dt.TimeZone)
            dt.TimeZone = "UTC";
        end
        return;
    end
    dt = datetime(double(simulation_start_time), "ConvertFrom", "posixtime", "TimeZone", "UTC");
end

function resolved = resolve_tle_file(tle_file)
%RESOLVE_TLE_FILE Locate a TLE file across standard project search directories.
%   RESOLVED = RESOLVE_TLE_FILE(TLE_FILE) checks whether TLE_FILE exists
%   directly, then searches the parent MATLAB folder, the function's own
%   directory, and the current working directory. Throws an error if not found.
    candidate = string(tle_file);
    if isfile(candidate)
        resolved = candidate;
        return;
    end

    % Build fallback candidate paths relative to the function location and pwd
    functionsDir = fileparts(mfilename("fullpath"));
    matlabDir = fileparts(functionsDir);
    searchPaths = [ ...
        fullfile(matlabDir, candidate), ...
        fullfile(functionsDir, candidate), ...
        fullfile(pwd, candidate)];
    for idx = 1:numel(searchPaths)
        if isfile(searchPaths(idx))
            resolved = searchPaths(idx);
            return;
        end
    end
    error("ntn_initialize_simulation:tle_not_found", "Unable to resolve TLE file: %s", candidate);
end