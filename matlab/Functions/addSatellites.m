function [SVs] = addSatellites(sc,varargin)
%ADDSATELLITES Import satellites from TLE data and attach Ka-band RF payloads.
%   SVS = ADDSATELLITES(SC) downloads daily Two-Line Element (TLE) orbital
%   data from AMSAT, imports the constellation into the satelliteScenario
%   object SC, and equips each satellite vehicle with gimbal-mounted Ka-band
%   transmitters and receivers using Gaussian dish antennas.
%
%   SVS = ADDSATELLITES(SC, TLEFILE) loads satellite orbital parameters from
%   the specified local TLE file instead of downloading from the web.
%
%   Syntax:
%       SVs = addSatellites(sc)
%       SVs = addSatellites(sc, tleFile)
%
%   Inputs:
%       sc       - satelliteScenario object to which satellites are added.
%       varargin - (Optional) Additional input arguments:
%                    * varargin{1} (tleFile) - Path or filename of a local
%                                              TLE file. If omitted, daily
%                                              TLE data is fetched from AMSAT.
%
%   Outputs:
%       SVs      - Array of Satellite objects added to the scenario.
%
%   Notes:
%       Each satellite is configured with separate Tx and Rx gimbals mounted
%       at [0; 0.5; 0] m and [0; -0.5; 0] m in the body frame, operating at
%       28 GHz (Ka-band) with 20 W transmit power, 100 Mbps bit rate, 3 dB
%       system loss, and 0.5 m Gaussian parabolic dish antennas.
%
%   See also SATELLITESCENARIO, SATELLITE, GIMBAL, TRANSMITTER, RECEIVER, GAUSSIANANTENNA.

    %% Orbital Data Acquisition (TLE)
    if(nargin == 1)
        % AMSAT daily TLE feed providing orbital elements for ~50 satellites
        tleURL = 'https://www.amsat.org/tle/dailytle.txt'; 
        
        % Define local destination file path for storing downloaded TLE data
        tleFile = tle('SatellitesOrbitalParameters.tle');
        
        % Download plain-text TLE data from the URL and save locally
        websave(tleFile, tleURL);
    else
        % Use user-supplied TLE file
        tleFile = varargin{1};
    end

    %% Constellation Initialization
    % Propagate and import satellites into the scenario from the TLE file
    SVs = satellite(sc,tleFile);
    numberSV = length(SVs);

    %% Satellite Payload and Link Budget Parameters
    % Typical Ka-band allocations: 17.7-20.2 GHz (downlink), 27.5-30.0 GHz (uplink).
    % Reference link budget values at 28 GHz (lambda = 1.07 cm):
    %   - Transmit power: 20 W (43 dBm / 13 dBW)
    %   - Antenna gain:   G = 10*log10(eta*(pi*D/lambda)^2) ~= 41 dBi
    %   - System losses:  3-5 dB typical
    PTx = 20;        % Transmit power [W]
    Rb = 100;        % Data bit rate [Mbps]
    fc = 28e9;       % Carrier frequency [Hz] (28 GHz, Ka-band)
    systemLoss = 3;  % RF front-end and feed system loss [dB]
    eta = 0.6;       % Aperture efficiency [dimensionless]

    %% Gimbal and Transceiver Configuration
    % Preallocate cell arrays for satellite gimbals, transmitters, and receivers
    gimbals = cell(1, numberSV);
    Txs     = cell(1, numberSV);
    Rxs     = cell(1, numberSV);

    for i = 1:numberSV
        % Mount independent Tx and Rx gimbals along the satellite body Y-axis
        gimbalTx = gimbal(SVs(i), ...    
            "MountingLocation",[0;0.5;0]);
        gimbalRx = gimbal(SVs(i), ...
            "MountingLocation",[0;-0.5;0]);

        % Attach Ka-band transmitter to the Tx gimbal
        Txs{i} = transmitter(gimbalTx, ...
            "Name", sprintf("Tx%d", i), ...
            "Frequency", fc, ...
            "Power", PTx, ...
            "BitRate", Rb, ...
            "SystemLoss",systemLoss);

        % Attach receiver to the Rx gimbal
        Rxs{i} = receiver(gimbalRx, ...
            "Name", sprintf("Rx%d", i), ...
            "SystemLoss",systemLoss);

        % Store Tx and Rx gimbal handles for the current satellite
        gimbals{i} = struct( ...
            "Tx",gimbalTx, ...
            "Rx",gimbalRx);
        
        % Assign 0.5 m Gaussian parabolic dish antennas to both transceivers
        dishDiameter = 0.5; % Antenna dish diameter [m]
        gaussianAntenna(Txs{i},DishDiameter=dishDiameter);
        gaussianAntenna(Rxs{i},DishDiameter=dishDiameter);
    end
end