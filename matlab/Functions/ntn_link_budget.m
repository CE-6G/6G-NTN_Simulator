function result = ntn_link_budget(reference_time)
%NTN_LINK_BUDGET Compute satellite positions and NTN link budget metrics at a given epoch.
%   RESULT = NTN_LINK_BUDGET(REFERENCE_TIME) queries the active simulation
%   state, evaluates satellite ECEF positions at REFERENCE_TIME, constructs
%   both inter-satellite links (ISL) and ground-to-satellite (D2D) links,
%   and computes propagation delay, slant distance, channel capacity, SNR,
%   link margin, path loss, and packet loss probability for every valid link.
%
%   Syntax:
%       result = ntn_link_budget(reference_time)
%
%   Inputs:
%       reference_time - Query epoch specified as a MATLAB datetime scalar or
%                        a numeric Unix POSIX timestamp [s].
%
%   Outputs:
%       result         - Scalar struct containing constellation state and link
%                        budget vectors:
%                          * timestamp              - Query timestamp [s].
%                          * satellites_id          - N-by-1 cell array of satellite IDs.
%                          * satellites_x           - N-by-1 ECEF X coordinates [km].
%                          * satellites_y           - N-by-1 ECEF Y coordinates [km].
%                          * satellites_z           - N-by-1 ECEF Z coordinates [km].
%                          * links_source           - L-by-1 cell array of source node IDs.
%                          * links_target           - L-by-1 cell array of target node IDs.
%                          * links_delay_ms         - L-by-1 total propagation + TEC delay [ms].
%                          * links_distance_km      - L-by-1 slant range [km].
%                          * links_capacity_mbps    - L-by-1 achievable link capacity [Mbps].
%                          * links_snr_db           - L-by-1 link SNR [dB].
%                          * links_margin_db        - L-by-1 link fade margin [dB].
%                          * links_elevation_deg    - L-by-1 link elevation angle [deg].
%                          * links_path_loss_db     - L-by-1 total path loss [dB].
%                          * links_packet_loss_prob - L-by-1 estimated packet loss probability.
%                          * link_valid_for_seconds - Estimated topology validity window [s].
%
%   Notes:
%       Requires prior initialization via ntn_initialize_simulation. Throws
%       'ntn_link_budget:not_initialized' if the persistent bridge state is
%       uninitialized.
%
%   See also NTN_BRIDGE_STATE, NTN_INITIALIZE_SIMULATION, NTN_SATELLITE_POSITIONS,
%            SATDISTANCES, GSSATDISTANCES, CHANNELMODEL, CHANNELDELAY, NTN_LINK_CAPACITY.

    %% Validate Persistent Simulation State
    state = ntn_bridge_state("get");
    if ~isfield(state, "initialized") || ~state.initialized
        error("ntn_link_budget:not_initialized", "Simulation has not been initialized");
    end

    %% Propagate Satellite Positions
    [satellites, satPosMeters] = ntn_satellite_positions(state, reference_time);
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
    
    % Return empty link arrays immediately if no satellites are active
    if isempty(satellites)
        result = struct( ...
            "timestamp", double(reference_time), ...
            "satellites_id", {sat_ids}, ...
            "satellites_x", sat_x, ...
            "satellites_y", sat_y, ...
            "satellites_z", sat_z, ...
            "links_source", {cell(0,1)}, ...
            "links_target", {cell(0,1)}, ...
            "links_delay_ms", zeros(0,1), ...
            "links_distance_km", zeros(0,1), ...
            "links_capacity_mbps", zeros(0,1), ...
            "links_snr_db", zeros(0,1), ...
            "links_margin_db", zeros(0,1), ...
            "links_elevation_deg", zeros(0,1), ...
            "links_path_loss_db", zeros(0,1), ...
            "links_packet_loss_prob", zeros(0,1), ...
            "link_valid_for_seconds", 20.0);
        return;
    end

    %% Construct Inter-Satellite and Ground-to-Satellite Links
    % Compute pairwise inter-satellite distances and evaluate RF ISL budgets
    distanceMatrixMeters = satDistances(satPosMeters);
    satSatLinks = build_links_from_distances(distanceMatrixMeters, satellites, state, reference_time);

    % Compute ground-to-satellite slant ranges and evaluate D2D access link budgets
    gsdistanceMatrixMeters = gsSatDistances(state.groundStations, satPosMeters);
    gsSatLinks = build_ground_satellite_links(state.groundStations, state.satellites, state, reference_time, gsdistanceMatrixMeters);

    % Merge ISL and ground-to-satellite link arrays
    links = [satSatLinks; gsSatLinks];
    
    % Estimate topology coherence duration based on worst-case link margin
    link_valid_for_seconds = estimate_validity_seconds(links, state);
    
    %% Unpack Link Struct Array into Column Vectors
    nLinks = numel(links);
    link_source = cell(nLinks, 1);
    link_target = cell(nLinks, 1);
    link_delay_ms = zeros(nLinks, 1);
    link_distance_km = zeros(nLinks, 1);
    link_capacity_mbps = zeros(nLinks, 1);
    link_snr_db = zeros(nLinks, 1);
    link_margin_db = zeros(nLinks, 1);
    link_elevation_deg = zeros(nLinks, 1);
    link_path_loss_db = zeros(nLinks, 1);
    link_packet_loss_prob = zeros(nLinks, 1);
    
    for idx = 1:nLinks
        link_source{idx} = char(links(idx).source);
        link_target{idx} = char(links(idx).target);
        link_delay_ms(idx) = links(idx).delay_ms;
        link_distance_km(idx) = links(idx).distance_km;
        link_capacity_mbps(idx) = links(idx).capacity_mbps;
        link_snr_db(idx) = links(idx).snr_db;
        link_margin_db(idx) = links(idx).margin_db;
        link_elevation_deg(idx) = links(idx).elevation_deg;
        link_path_loss_db(idx) = links(idx).path_loss_db;
        link_packet_loss_prob(idx) = links(idx).packet_loss_prob;
    end
    
    %% Package Output Structure
    result = struct( ...
        "timestamp", double(reference_time), ...
        "satellites_id", {sat_ids}, ...
        "satellites_x", sat_x, ...
        "satellites_y", sat_y, ...
        "satellites_z", sat_z, ...
        "links_source", {link_source}, ...
        "links_target", {link_target}, ...
        "links_delay_ms", link_delay_ms, ...
        "links_distance_km", link_distance_km, ...
        "links_capacity_mbps", link_capacity_mbps, ...
        "links_snr_db", link_snr_db, ...
        "links_margin_db", link_margin_db, ...
        "links_elevation_deg", link_elevation_deg, ...
        "links_path_loss_db", link_path_loss_db, ...
        "links_packet_loss_prob", link_packet_loss_prob, ...
        "link_valid_for_seconds", link_valid_for_seconds);
end

%% Local Helper Functions

function links = build_links_from_distances(distanceMatrixMeters, satellites, ~, reference_time)
%BUILD_LINKS_FROM_DISTANCES Construct RF inter-satellite links from pairwise ranges.
%   LINKS = BUILD_LINKS_FROM_DISTANCES(DISTANCEMATRIXMETERS, SATELLITES, ~, REFERENCE_TIME)
%   evaluates free-space RF link budgets between all visible satellite pairs,
%   retains up to the top 4 highest-SNR neighbors per spacecraft, and deduplicates
%   bidirectional pairs into undirected edges (source < target, 0-indexed IDs).
    nSat = numel(satellites);
    template = struct( ...
        "source", "", ...
        "target", "", ...
        "delay_ms", 0, ...
        "distance_km", 0, ...
        "capacity_mbps", 0, ...
        "snr_db", 0, ...
        "margin_db", 0, ...
        "elevation_deg", 0, ...
        "path_loss_db", 0, ...
        "packet_loss_prob", 0);
    links = repmat(template, 0, 1);

    % ISL RF parameters and physical constants
    freqGHz = 28;                 % Carrier frequency [GHz] (Ka-band)
    c_km_per_ms = 299.792458;     % Speed of light [km/ms]
    sampleTime = to_datetime(reference_time);
    
    % Evaluate candidate neighbor links for each satellite
    all_possible_links = cell(nSat, 1);
    for i = 1:nSat
        sat_link_candidates = [];
        for j = 1:nSat
            if i == j
                continue;
            end
            
            distanceMeters = distanceMatrixMeters(i, j);
            if isnan(distanceMeters)
                continue;
            end

            % Compute geometric propagation delay and effective grazing angle
            distanceKm = distanceMeters / 1000.0;
            delayMs = distanceKm / c_km_per_ms;
            elevationDeg = max(5.0, 90.0 - min(distanceKm / 8000.0 * 75.0, 75.0));

            % Evaluate free-space path loss (flag = 1 bypasses atmospheric attenuation)
            channel = channelModel(freqGHz, elevationDeg, 1.0, "standard", distanceMeters, 1);
            [~, delayTEC] = channelDelay(freqGHz, 7.5, elevationDeg, sampleTime);

            candidate = struct( ...
                "source", string(i - 1), ...
                "target", string(j - 1), ...
                "delay_ms", delayMs + double(delayTEC) * 1e3, ...
                "distance_km", distanceKm, ...
                "capacity_mbps", ntn_link_capacity(channel.snr_margin_db, 'ISL_RF')/1e6, ...
                "snr_db", channel.snr_margin_db, ...
                "margin_db", channel.snr_margin_db, ...
                "elevation_deg", elevationDeg, ...
                "path_loss_db", channel.path_loss_db, ...
                "packet_loss_prob", max(0.0, min(1.0, exp(-max(channel.snr_margin_db, 0.0) / 5.0))));
                
            sat_link_candidates = [sat_link_candidates; candidate];
        end
        
        % Keep at most the 4 strongest ISL candidates per satellite ranked by SNR
        if ~isempty(sat_link_candidates)
            [~, sorted_snr_idx] = sort([sat_link_candidates.snr_db], 'descend');
            num_to_keep = min(4, length(sorted_snr_idx));
            all_possible_links{i} = sat_link_candidates(sorted_snr_idx(1:num_to_keep));
        end
    end

    % Deduplicate symmetric pairs to emit unique links with source < target
    linkCount = 0;
    for i = 1:nSat
        candidates = all_possible_links{i};
        for k = 1:length(candidates)
            src = str2double(candidates(k).source);
            tgt = str2double(candidates(k).target);
            
            if src < tgt
                linkCount = linkCount + 1;
                links(linkCount, 1) = candidates(k);
            end
        end
    end
end

function validity = estimate_validity_seconds(links, state)
%ESTIMATE_VALIDITY_SECONDS Estimate duration for which current link metrics remain valid.
%   VALIDITY = ESTIMATE_VALIDITY_SECONDS(LINKS, STATE) scales the baseline
%   validity interval using the minimum link margin across active links and
%   clamps the result to [5.0, 60.0] seconds.
    if isempty(links)
        validity = state.linkValidBaseSeconds;
        return;
    end
    snrValues = [links.snr_db];
    marginValues = [links.margin_db];
    if isempty(snrValues)
        validity = state.linkValidBaseSeconds;
        return;
    end
    worstMargin = min(marginValues);
    validity = state.linkValidBaseSeconds + max(0.0, worstMargin) * 2.0;
    validity = max(5.0, min(60.0, validity));
end

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

function links = build_ground_satellite_links(groundStations, satellitesObj, state, reference_time, gsdistanceMatrixMeters)
%BUILD_GROUND_SATELLITE_LINKS Construct ground-to-satellite (D2D) link entries.
%   LINKS = BUILD_GROUND_SATELLITE_LINKS(GROUNDSTATIONS, SATELLITESOBJ, STATE, REFERENCE_TIME, GSDISTANCEMATRIXMETERS)
%   evaluates atmospheric and free-space link budgets for all visible
%   ground-station-to-satellite pairs within the slant-range horizon.
    template = struct( ...
        "source", "", ...
        "target", "", ...
        "delay_ms", 0, ...
        "distance_km", 0, ...
        "capacity_mbps", 0, ...
        "snr_db", 0, ...
        "margin_db", 0, ...
        "elevation_deg", 0, ...
        "path_loss_db", 0, ...
        "packet_loss_prob", 0);
    links = repmat(template, 0, 1);
    if isempty(groundStations) || isempty(satellitesObj)
        return;
    end

    % Ground-to-satellite link parameters and physical constants
    freqGHz = state.frequencyGHz;     % Carrier frequency [GHz]
    c_km_per_ms = 299.792458;         % Speed of light [km/ms]
    queryTime = to_datetime(reference_time);
    satVelocity_kmps = 7.5;           % Nominal LEO orbital velocity [km/s]
    rainRate_mmph = 1.0;              % Nominal rain rate [mm/h]
    linkCount = 0;
    nGS = numel(groundStations);
    nSat = numel(satellitesObj);

    for g = 1:nGS
        minEl = groundStations(g).MinElevationAngle;
        for s = 1:nSat
            distanceMeters = gsdistanceMatrixMeters(g,s);
            if isnan(distanceMeters)
                continue;
            end

            % Use fixed nominal elevation angle (10 deg) to avoid the high
            % computational overhead (>20 s) of per-pair aer() calls
            el = 10;
            %{
            try
                [~, el, ~] = aer(groundStations(g), satellitesObj(s), queryTime);
            catch
                continue;
            end
            el = double(el);
            if isempty(el) || isnan(el)
                continue;
            end
            if el < minEl
                continue;
            end
            %}

            % Compute slant range, geometric propagation delay, and ITU-R channel loss
            distanceKm = distanceMeters / 1000.0;
            delayMs = distanceKm / c_km_per_ms;
            channel = channelModel(freqGHz, el, rainRate_mmph, "standard", distanceMeters, 0);
            [~, delayTEC] = channelDelay(freqGHz, satVelocity_kmps, el, queryTime);

            linkCount = linkCount + 1;
            links(linkCount, 1) = struct( ...
                "source", groundStations(g).Name, ...
                "target",  string(s - 1), ...
                "delay_ms", delayMs + double(delayTEC) * 1e3, ...
                "distance_km", distanceKm, ...
                "capacity_mbps", ntn_link_capacity(channel.snr_margin_db, 'D2D')/1e6, ...
                "snr_db", channel.snr_margin_db, ...
                "margin_db", channel.snr_margin_db, ...
                "elevation_deg", el, ...
                "path_loss_db", channel.path_loss_db, ...
                "packet_loss_prob", max(0.0, min(1.0, exp(-max(channel.snr_margin_db, 0.0) / 5.0))));
        end
    end
end