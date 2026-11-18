function [dopplerShift,delayTEC] = channelDelay(freqGHz, satVelocity_kmps, elevation_deg,localTime)
%CHANNELDELAY Compute satellite Doppler shift metrics and ionospheric TEC delay.
%   [DOPPLERSHIFT, DELAYTEC] = CHANNELDELAY(FREQGHz, SATVELOCITY_KMPS, ELEVATION_DEG)
%   calculates first-order Doppler frequency shift parameters and diurnal
%   ionospheric group delay due to Total Electron Content (TEC) for a
%   satellite-to-ground link using the current UTC time.
%
%   [DOPPLERSHIFT, DELAYTEC] = CHANNELDELAY(FREQGHz, SATVELOCITY_KMPS, ELEVATION_DEG, LOCALTIME)
%   evaluates the diurnal TEC profile at the specified LOCALTIME timestamp.
%
%   Syntax:
%       [dopplerShift, delayTEC] = channelDelay(freqGHz, satVelocity_kmps, elevation_deg)
%       [dopplerShift, delayTEC] = channelDelay(freqGHz, satVelocity_kmps, elevation_deg, localTime)
%
%   Inputs:
%       freqGHz          - Carrier frequency in gigahertz [GHz].
%       satVelocity_kmps - Satellite orbital speed in kilometers per second [km/s].
%       elevation_deg    - Ground station elevation angle to the satellite [deg].
%       localTime        - (Optional) datetime scalar used to determine the
%                          diurnal ionospheric TEC level. Defaults to the
%                          current UTC time if omitted or empty.
%
%   Outputs:
%       dopplerShift     - Scalar struct containing Doppler metrics:
%                            * doppler_hz     - Instantaneous Doppler shift [Hz].
%                            * doppler_ppm    - Normalized Doppler shift [ppm].
%                            * max_doppler_hz - Magnitude of Doppler shift [Hz].
%                            * min_cycle_ms   - Minimum Doppler cycle period [ms].
%                            * rate_hz_s      - Doppler drift rate [Hz/s].
%                            * v_radial_km_s  - Line-of-sight radial velocity [km/s].
%                            * freq_ghz       - Input carrier frequency [GHz].
%                            * elevation_deg  - Input elevation angle [deg].
%       delayTEC         - First-order ionospheric excess group delay [s].
%
%   Notes:
%       Doppler drift rate assumes a circular LEO orbit at 550 km altitude
%       (Earth equatorial radius = 6,378,137 m). Ionospheric group delay is
%       modeled using the first-order dispersive relation:
%           tau_TEC = (40.3 * TEC) / (f0^2 * c)
%       where TEC [el/m^2] follows a piecewise diurnal step profile.
%
%   See also DATETIME, HOUR, DEG2RAD.

    %% Input Validation and Defaults
    if nargin < 1, error('freq_ghz is required'); end
    if nargin < 2, error('sat_velocity_km_s is required'); end
    if nargin < 3, error('elevation_deg is required'); end
    if nargin < 4 || isempty(localTime)          
        localTime = datetime('now', 'TimeZone', 'UTC');
    end

    %% Physical Constants and Unit Conversions
    c = 299792458.0;                 % Speed of light in vacuum [m/s]
    f0 = freqGHz * 1e9;              % Carrier frequency [Hz]
    v = satVelocity_kmps * 1000.0;   % Satellite orbital speed [m/s]
    elev_rad = deg2rad(elevation_deg); % Elevation angle [rad]

    %% Doppler Shift and Drift Rate Calculation
    % Project satellite velocity vector onto the line-of-sight direction
    v_radial = v * cos(elev_rad);         % Radial velocity component [m/s]
    doppler_hz = f0 * v_radial / c;       % Classical Doppler shift [Hz]
    doppler_ppm = doppler_hz / f0 * 1e6;  % Fractional Doppler offset [ppm]

    % Estimate Doppler rate assuming a 550 km circular LEO orbit
    r_orbit = 6378137 + 550e3;            % Geocentric orbital radius [m]
    omega = v / r_orbit;                  % Orbital angular velocity [rad/s]
    rate_hz_s = -f0 * v^2 * omega * sin(elev_rad) / c; % Doppler rate [Hz/s]

    % Package Doppler metrics into output structure
    dopplerShift = struct(...
        'doppler_hz', doppler_hz, ...
        'doppler_ppm', doppler_ppm, ...
        'max_doppler_hz', abs(doppler_hz), ...
        'min_cycle_ms', 1e3 / max(abs(doppler_hz), 1), ...
        'rate_hz_s', rate_hz_s, ...
        'v_radial_km_s', v_radial / 1000.0, ...
        'freq_ghz', freqGHz, ...
        'elevation_deg', elevation_deg);

    %% Diurnal Ionospheric TEC Model and Group Delay
    % Select piecewise Total Electron Content (TEC) [electrons/m^2] by hour of day
    currentHour = hour(localTime);
    if(currentHour <= 6)
        TEC = 3e18;
    elseif((6 < currentHour) && (currentHour <= 8))
        TEC = 10e18;
    elseif((8 < currentHour) && (currentHour < 11))
        TEC = 30e18;
    elseif((11 < currentHour) && (currentHour < 13))
        TEC = 45e18;
    elseif((13 < currentHour) && (currentHour < 15))
        TEC = 30e18;
    elseif((15 < currentHour) && (currentHour < 18))
        TEC = 10e18;
    elseif((18 < currentHour) && (currentHour < 23))
        TEC = 3e18;
    else
        TEC = 2e18;
    end

    % First-order ionospheric excess propagation delay [s]
    delayTEC = (40.3*TEC) / ((f0^2)*c);
end