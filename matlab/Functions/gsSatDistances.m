function D = gsSatDistances(groundStations, satPositions, varargin)
%GSSATDISTANCES Compute pairwise slant ranges between ground stations and satellites.
%   D = GSSATDISTANCES(GROUNDSTATIONS, SATPOSITIONS) converts ground station
%   geodetic coordinates (WGS84) to Earth-Centered, Earth-Fixed (ECEF)
%   Cartesian coordinates and computes the pairwise Euclidean slant range
%   to each satellite position. Ranges exceeding 2000 km are masked as NaN.
%
%   D = GSSATDISTANCES(GROUNDSTATIONS, SATPOSITIONS, F) adds a first-order
%   ionospheric excess group path length (assuming 50 TECU) evaluated at
%   carrier frequency F [Hz].
%
%   Syntax:
%       D = gsSatDistances(groundStations, satPositions)
%       D = gsSatDistances(groundStations, satPositions, f)
%
%   Inputs:
%       groundStations - M-by-1 array of GroundStation objects or structs,
%                        each containing fields:
%                          * Latitude  - Geodetic latitude [deg].
%                          * Longitude - Geodetic longitude [deg].
%                          * Altitude  - (Optional) Ellipsoidal height [m]
%                                        (default: 0).
%       satPositions   - N-by-3 matrix of satellite ECEF coordinates [m].
%       varargin       - (Optional) Additional input arguments:
%                          * varargin{1} (f) - Carrier frequency [Hz] used
%                                              to include a first-order
%                                              ionospheric path delay term.
%
%   Outputs:
%       D              - M-by-N matrix of slant ranges [m], where D(g,s) is
%                        the distance from ground station g to satellite s.
%                        Entries greater than 2000 km are set to NaN.
%
%   Notes:
%       Line-of-sight visibility and Earth occultation are not checked; only
%       a maximum slant-range cutoff of 2000 km is applied.
%
%   See also DEG2RAD, LLA2ECEF.

    %% Early Exit for Empty Inputs
    if isempty(groundStations) || isempty(satPositions)
        D = zeros(numel(groundStations), size(satPositions,1));
        return;
    end

    %% Extract Geodetic Coordinates
    M = numel(groundStations);
    latDeg = zeros(M,1);
    lonDeg = zeros(M,1);
    altM   = zeros(M,1);
    for g = 1:M
        gs = groundStations(g);
        latDeg(g) = gs.Latitude;
        lonDeg(g) = gs.Longitude;
        % Default altitude to sea level (0 m) if the Altitude field is absent
        if isfield(gs, 'Altitude')
            altM(g) = gs.Altitude;
        else
            altM(g) = 0;
        end
    end

    %% Geodetic-to-ECEF Coordinate Transformation (WGS84)
    a  = 6378137.0;              % WGS84 semi-major axis [m]
    e2 = 6.69437999014e-3;       % WGS84 first eccentricity squared

    % Convert geodetic angles from degrees to radians
    lat = deg2rad(latDeg);
    lon = deg2rad(lonDeg);

    % Precompute trigonometric terms
    sinLat = sin(lat);
    cosLat = cos(lat);
    sinLon = sin(lon);
    cosLon = cos(lon);

    % Compute prime vertical radius of curvature [m]
    N = a ./ sqrt(1 - e2 * sinLat.^2);

    % Compute ECEF Cartesian coordinates [m]
    h = altM;
    gsX = (N + h) .* cosLat .* cosLon;
    gsY = (N + h) .* cosLat .* sinLon;
    gsZ = (N .* (1 - e2) + h) .* sinLat;

    % Assemble M-by-3 matrix of ground station ECEF positions [m]
    gsPositions = [gsX, gsY, gsZ];

    %% Vectorized Pairwise Euclidean Slant Range Calculation
    gsNorm2  = sum(gsPositions.^2, 2);      % M-by-1 squared norms of ground stations
    satNorm2 = sum(satPositions.^2, 2)';    % 1-by-N squared norms of satellites
    
    % Expand ||x - y||^2 = ||x||^2 + ||y||^2 - 2*(x . y)
    D2 = gsNorm2 + satNorm2 - 2 * (gsPositions * satPositions');
    D2(D2 < 0) = NaN;  % Guard against negative values from floating-point rounding
    D = sqrt(D2);

    %% Range Filtering and Optional Ionospheric Delay
    % Optional K-nearest neighbor pruning (retained for reference):
    %{
    for ii = 1:M
        d_flat = D(ii,:);
        d_valid = d_flat(~isnan(d_flat));
        
        if numel(d_valid) <= 10
            % Fewer than 10 valid distances: keep everything
            return;
        end
        % Sort and find the 10th smallest value
        d_sorted = sort(d_valid, 'ascend');
        threshold = d_sorted(10);
        % Set all distances greater than the threshold to NaN
        D(ii, D(ii,:) > threshold) = NaN;
    end
    %}

    % Mask slant ranges beyond the 2000 km LEO visibility horizon
    D(D>2000e3) = NaN;

    % Add first-order ionospheric excess group range [m] if carrier frequency is provided
    if nargin >= 3
        f = varargin{1};
        TEC_val = 50; % Assumed Total Electron Content [TECU, 10^16 el/m^2]
        Delay_mat = (40.3 * TEC_val * 1e16) ./ (f^2);
        D = D + Delay_mat;
    end
end