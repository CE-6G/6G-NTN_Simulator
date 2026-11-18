function [GS] = addGroundStations(sc, ground_nodes, varargin)
%ADDGROUNDSTATIONS Add ground stations with RF transceivers to a satellite scenario.
%   GS = ADDGROUNDSTATIONS(SC, GROUND_NODES) adds ground stations to the
%   satelliteScenario object SC using node definitions provided in GROUND_NODES.
%   If GROUND_NODES is omitted, empty, or invalid JSON, two default user
%   equipment (UE) ground stations (UE1 and UE2) are created without
%   attached transmitters or receivers.
%
%   Syntax:
%       GS = addGroundStations(sc)
%       GS = addGroundStations(sc, ground_nodes)
%       GS = addGroundStations(sc, ground_nodes, varargin)
%
%   Inputs:
%       sc           - satelliteScenario object to which ground stations
%                      are added.
%       ground_nodes - (Optional) Struct array or JSON-encoded char/string
%                      defining ground nodes. Each element must contain the
%                      following fields:
%                        * lat_deg           - Geodetic latitude in degrees.
%                        * lon_deg           - Geodetic longitude in degrees.
%                        * node_id           - Unique identifier/name for the node.
%                        * MinElevationAngle - (Optional) Minimum elevation
%                                              angle in degrees (default: 10).
%       varargin     - (Optional) Additional input arguments reserved for
%                      future extensions.
%
%   Outputs:
%       GS           - Array of GroundStation objects added to the scenario.
%
%   Notes:
%       Configured RF parameters target Ka-band (28 GHz) links using a
%       microstrip patch antenna, 20 W transmit power, and 100 Mbps bit rate.
%
%   See also SATELLITESCENARIO, GROUNDSTATION, TRANSMITTER, RECEIVER, PATCHMICROSTRIP.

    %% RF and Link Budget Configuration
    % Minimum elevation angle depends on the deployment environment (e.g.,
    % urban areas require higher angles to mitigate multipath and Line-of-Sight
    % blockage). Below this threshold, signals are assumed degraded by obstacles.
    minElevationAngle = 10; % Minimum elevation angle [deg]

    % Carrier and transceiver parameters (Ka-band 5G/6G NTN reference values)
    % Note: Standard UE power classes are Class 3 (23 dBm / 0.2 W) and
    % Class 2 (26 dBm / 0.4 W); higher power is set here for link analysis.
    fc = 28e9;   % Carrier frequency [Hz] (28 GHz, Ka-band)
    PTx = 20;    % Transmit power [W]
    Rb = 100;    % Data bit rate [Mbps]

    % Link budget reference parameters (typical ranges: NF 5-9 dB, Gain 8-12 dBi,
    % ground station losses 1.5-3 dB, required SNR_min +5 to +12 dB; thermal
    % noise floor -174 dBm/Hz from Pn = k*T*B yielding ~ -81 dBm sensitivity)
    F = 7;       % Receiver noise figure [dB]
    Gain = 10;   % Nominal antenna gain [dBi]

    % Design a resonant microstrip patch antenna at the carrier frequency
    patchAntenna = design(patchMicrostrip,fc);

    %% Parse Input Node Definitions
    % Decode JSON string or character vector into a MATLAB struct array
    if ischar(ground_nodes) || isstring(ground_nodes)
        try
            ground_nodes = jsondecode(char(ground_nodes));
        catch
            % Fallback to default ground station configuration on parse error
            ground_nodes = [];
        end
    end

    %% Ground Station Creation
    if nargin < 2 || isempty(ground_nodes)
        % Create default two-node test configuration (UE1: Tx, UE2: Rx)
        latitude1 = 45.0579;
        longitude1 = 7.6566;
        GS1 = groundStation(sc,Latitude=latitude1,Longitude=longitude1,MinElevationAngle=minElevationAngle,Name='UE1');

        latitude2 = 64.1285;
        longitude2 = -21.9377;
        GS2 = groundStation(sc,Latitude=latitude2,Longitude=longitude2,MinElevationAngle=minElevationAngle,Name='UE2');

        GS = [GS1,GS2];
    else
        % Preallocate empty GroundStation object array
        GS(1) = groundStation(sc);  % Temporary object for type initialization
        GS(1) = [];

        % Instantiate each ground station and attach Ka-band RF transceivers
        for id = 1:numel(ground_nodes)
            node = ground_nodes(id);
            lat = node.lat_deg;
            lon = node.lon_deg;

            % Use node-specific minimum elevation angle if provided
            if isfield(node, "MinElevationAngle")
                minEl = node.MinElevationAngle;
            else
                minEl = minElevationAngle;
            end

            % Add ground station to the satellite scenario
            GS_new = groundStation(sc,Latitude=lat,Longitude=lon,MinElevationAngle=minEl,Name=node.node_id);

            % Attach Ka-band transmitter
            transmitter(GS_new, ...
                "Name", string(node.node_id), ...
                "Frequency", fc, ...
                "Power", PTx, ...
                "BitRate", Rb, ...
                "Antenna", patchAntenna);

            % Attach Ka-band receiver
            receiver(GS_new,...
                "Name", string(node.node_id), ...
                "Antenna", patchAntenna);

            % Append configured ground station to the output array
            GS(id) = GS_new;
        end
    end