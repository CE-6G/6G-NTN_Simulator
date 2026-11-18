function result = channelModel(freqGHz, elevation_deg, rainRate_mmph, atmModel, D, flag)
%CHANNELMODEL Approximate ITU-R P.618/P.676/P.838 satellite channel attenuation model.
%   RESULT = CHANNELMODEL(FREQGHz, ELEVATION_DEG, RAINRATE_MMPH, ATMMODEL, D, FLAG)
%   computes free-space path loss and approximate atmospheric propagation
%   impairments—including gaseous (oxygen and water vapor) absorption, rain
%   attenuation, and tropospheric scintillation—for an Earth-space link.
%
%   Syntax:
%       result = channelModel(freqGHz, elevation_deg, rainRate_mmph, atmModel, D, flag)
%
%   Inputs:
%       freqGHz       - Carrier frequency in gigahertz [GHz].
%       elevation_deg - Ground station elevation angle [deg] (clamped to >= 0.1 deg).
%       rainRate_mmph - (Optional) Point rainfall rate [mm/h] (default: 1.0).
%       atmModel      - (Optional) Atmospheric profile label (default: 'standard').
%       D             - Slant path distance [m] used for free-space path loss.
%       flag          - Logical/numeric mode flag:
%                         * false (0) - Include atmospheric, rain, scintillation,
%                                       and free-space path losses.
%                         * true  (1) - Free-space path loss only (bypass
%                                       atmospheric impairments).
%
%   Outputs:
%       result        - Scalar struct containing link attenuation and margin terms:
%                         * atm_loss_db      - Gaseous absorption loss (O2 + H2O) [dB].
%                         * rain_loss_db     - Rain attenuation loss [dB].
%                         * scintillation_db - Tropospheric scintillation loss [dB].
%                         * total_loss_db    - Total propagation path loss [dB].
%                         * snr_margin_db    - Link SNR margin relative to 8.0 dB [dB].
%                         * path_loss_db     - Alias of total_loss_db [dB].
%                         * model            - Atmospheric model label string.
%                         * freq_ghz         - Input carrier frequency [GHz].
%                         * elev_deg         - Clamped elevation angle [deg].
%
%   Example:
%       r = channelModel(20, 35, 5, 'standard', 550e3, false);
%
%   References:
%       [1] ITU-R Recommendation P.618: Propagation data and prediction methods
%           required for the design of Earth-space telecommunication systems.
%       [2] ITU-R Recommendation P.676: Attenuation by atmospheric gases and
%           related effects.
%       [3] ITU-R Recommendation P.838: Specific attenuation model for rain
%           for use in prediction methods.
%
%   See also DEG2RAD, SIND, INTERP1.

    %% Free-Space Path Loss and Input Validation
    c = 299792458; % Speed of light in vacuum [m/s]
    free_path_loss_db = 10*log10(((4 * pi * D * freqGHz) / c).^2);

    if nargin < 1, error('freq_ghz is required'); end
    if nargin < 2, error('elev_deg is required'); end
    if nargin < 3 || isempty(rainRate_mmph), rainRate_mmph = 1.0; end
    if nargin < 4 || isempty(atmModel), atmModel = 'standard'; end

    % Enforce minimum elevation angle to prevent slant-path singularities
    elevation_deg = max(elevation_deg, 0.1);
    elev_rad = deg2rad(elevation_deg);

    %% Atmospheric Gaseous Absorption (ITU-R P.676 Approximation)
    oxy = oxygen_attenuation(freqGHz);         % Specific dry-air attenuation [dB/km]
    wv = water_vapour_attenuation(freqGHz);    % Specific water vapor attenuation [dB/km]
    slant_km = 5.0 / max(sin(elev_rad), 0.05); % Effective atmospheric slant path [km]
    atm_loss_db = (oxy + wv) * slant_km;       % Total gaseous attenuation [dB]

    %% Rain Attenuation and Tropospheric Scintillation (ITU-R P.618 / P.838)
    rain_loss_db = rain_attenuation_618(freqGHz, elevation_deg, rainRate_mmph);
    scintillation_db = 0.2 + 0.01 * freqGHz; % Tropospheric scintillation fade [dB]

    %% Total Path Loss and Link Margin Aggregation
    if(~flag)
        total_loss_db = atm_loss_db + rain_loss_db + scintillation_db + free_path_loss_db;
    else
        total_loss_db = free_path_loss_db;
    end
    path_loss_db = total_loss_db;
    snr_margin_db = 8.0 - total_loss_db; % Margin against 8.0 dB nominal budget [dB]

    %% Package Output Structure
    result = struct(...
        'atm_loss_db', atm_loss_db, ...
        'rain_loss_db', rain_loss_db, ...
        'scintillation_db', scintillation_db, ...
        'total_loss_db', total_loss_db, ...
        'snr_margin_db', snr_margin_db, ...
        'path_loss_db', path_loss_db, ...
        'model', atmModel, ...
        'freq_ghz', freqGHz, ...
        'elev_deg', elevation_deg);
end

%% Local Helper Functions

function y = oxygen_attenuation(f)
%OXYGEN_ATTENUATION Approximate specific attenuation due to dry air (oxygen).
%   Y = OXYGEN_ATTENUATION(F) returns specific attenuation [dB/km] at
%   frequency F [GHz], modeling the 60 GHz oxygen absorption complex.
    y = 0.005 + 0.0002 * f.^2 ./ (1 + ((f - 60) ./ 18).^2);
end

function y = water_vapour_attenuation(f)
%WATER_VAPOUR_ATTENUATION Approximate specific attenuation due to water vapor.
%   Y = WATER_VAPOUR_ATTENUATION(F) returns specific attenuation [dB/km] at
%   frequency F [GHz], capturing the 22.235 GHz and 183.31 GHz resonance lines.
    line1 = 0.002 * exp(-((f - 22.235) / 8).^2);
    line2 = 0.006 * exp(-((f - 183.31) / 20).^2);
    y = 0.001 + line1 + line2;
end

function a = rain_attenuation_618(freq_ghz, elev_deg, rain_rate)
%RAIN_ATTENUATION_618 Compute slant-path rain attenuation per ITU-R P.618/P.838.
%   A = RAIN_ATTENUATION_618(FREQ_GHZ, ELEV_DEG, RAIN_RATE) evaluates specific
%   rain attenuation gamma_R = k * R^alpha using horizontal polarization
%   coefficients and scales by an effective slant path length.
    [k, alpha] = itu_r_p838_coeffs(freq_ghz, 'H');
    gamma_r = k * rain_rate.^alpha;
    effective_path = 2.0 / max(sind(elev_deg), 0.1);
    a = gamma_r * effective_path;
end

function [k, alpha] = itu_r_p838_coeffs(f, pol)
%ITU_R_P838_COEFFS Interpolate ITU-R P.838 rain specific attenuation coefficients.
%   [K, ALPHA] = ITU_R_P838_COEFFS(F, POL) performs log-linear interpolation
%   of power-law coefficients K and ALPHA over 1-100 GHz for vertical ('V')
%   or horizontal ('H') polarization.
    freqs = [1 2 4 8 12 20 30 40 60 80 100];
    k_h = [0.0001 0.0002 0.0006 0.002 0.006 0.06 0.16 0.32 0.9 1.3 1.7];
    a_h = [1.0 1.0 1.0 1.0 1.05 1.08 1.12 1.16 1.2 1.25 1.3];
    k_v = [0.0001 0.0002 0.0005 0.0018 0.005 0.05 0.14 0.28 0.8 1.2 1.6];
    a_v = [1.0 1.0 1.0 1.0 1.04 1.07 1.10 1.14 1.18 1.23 1.28];
    if strcmpi(pol, 'V')
        k = exp(interp1(log(freqs), log(k_v), log(max(min(f,100),1)), 'linear'));
        alpha = interp1(log(freqs), a_v, log(max(min(f,100),1)), 'linear');
    else
        k = exp(interp1(log(freqs), log(k_h), log(max(min(f,100),1)), 'linear'));
        alpha = interp1(log(freqs), a_h, log(max(min(f,100),1)), 'linear');
    end
end