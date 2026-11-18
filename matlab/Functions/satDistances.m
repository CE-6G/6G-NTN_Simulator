function [D] = satDistances(satPositions,varargin)
%SATDISTANCES Compute pairwise inter-satellite slant ranges with Earth occultation masking.
%   D = SATDISTANCES(SATPOSITIONS) computes the N-by-N matrix of pairwise
%   Euclidean distances [m] between satellites given their Earth-Centered,
%   Earth-Fixed (ECEF) Cartesian coordinates SATPOSITIONS [m]. Pairs whose
%   line-of-sight (LOS) path intersects the Earth or its lower atmosphere
%   (effective blocking radius = 6421 km) are masked as NaN, along with the
%   main diagonal.
%
%   D = SATDISTANCES(SATPOSITIONS, ~, F) adds a first-order ionospheric
%   excess group path length (assuming 50 TECU) evaluated at carrier
%   frequency F [Hz] when three input arguments are provided.
%
%   Syntax:
%       D = satDistances(satPositions)
%       D = satDistances(satPositions, varargin)
%
%   Inputs:
%       satPositions - N-by-3 matrix of satellite ECEF coordinates [X, Y, Z]
%                      in meters [m].
%       varargin     - (Optional) Additional input arguments:
%                        * varargin{1} (f) - Carrier frequency [Hz] used to
%                                            apply a first-order ionospheric
%                                            excess path term when nargin == 3.
%
%   Outputs:
%       D            - N-by-N symmetric matrix of inter-satellite slant ranges
%                      [m]. Non-LOS (Earth-occulted) pairs and self-pairs on
%                      the main diagonal are set to NaN.
%
%   Notes:
%       Line-of-sight visibility is determined geometrically by comparing the
%       central geocentric angle theta(i,j) between satellites i and j against
%       the sum of their tangent horizon angles alpha(i) + alpha(j) relative to
%       an effective Earth + atmosphere radius R_eff = (6371 + 50) km.
%
%   See also VECNORM, ACOS, GSSATDISTANCES.

    %% Legacy Reference Implementation (No Earth Occultation)
    % Previous broadcasting implementation without geometric LOS masking:
    % S1 = permute(satPositions,[1 3 2]);   % 3 x N x 1
    % S2 = permute(satPositions,[1 2 3]);   % 3 x 1 x N
    % diff = abs(S1 - S2);       % broadcasting -> 3 x N x N
    % D = squeeze(sum(diff,1));  % N x N

    %% Effective Earth + Atmosphere Blocking Radius
    % R_Earth = 6371 km, H_atm = 50 km (typical atmospheric grazing altitude 50-80 km)
    R_eff = (6371 + 50)*1e3; % Effective blocking sphere radius [m]

    %% Geocentric Radii and Horizon Tangent Angles
    % Compute geocentric orbital radius [m] for each satellite (row-wise 2-norm)
    r = vecnorm(satPositions, 2, 2);

    % Compute maximum central angle alpha(i) from nadir to the R_eff tangent horizon [rad]
    alpha = acos(R_eff ./ r);

    % Pairwise maximum visible central angle matrix: alphaMax(i,j) = alpha(i) + alpha(j) [rad]
    alphaMax = alpha + alpha'; 

    %% Central Geocentric Angles and Line-of-Sight (LOS) Mask
    % Compute pairwise inner products of ECEF position vectors (N-by-N) [m^2]
    dotProd = satPositions * satPositions'; 

    % Evaluate cosine of the central angle between each satellite pair
    cosTheta = dotProd ./ (r * r');

    % Clamp cosTheta to [-1, 1] to prevent complex outputs from floating-point drift
    cosTheta(cosTheta > 1) = 1; 
    cosTheta(cosTheta < -1) = -1;    

    % Pairwise central geocentric angles [rad]
    theta = acos(cosTheta);    

    % Boolean LOS visibility mask: true when grazing altitude exceeds R_eff
    LOS = theta < alphaMax;    

    %% Pairwise Euclidean Slant Range Calculation and Masking
    % Evaluate inter-satellite slant ranges via the law of cosines [m]
    D = sqrt(r.^2 + (r').^2 - 2 .* (r * r') .* cosTheta);    

    % Mask Earth-occulted pairs and zero-distance self-pairs along the diagonal
    D(~LOS) = NaN;    
    D(logical(eye(size(D)))) = NaN;    

    %% Optional Ionospheric Excess Group Path Delay
    if(nargin == 3)
        f = varargin{1};
        TEC_val = 50; % Assumed Total Electron Content [TECU, 10^16 el/m^2]
        Delay_mat = (40.3 * TEC_val * 1e16) ./ (f^2);
        D = D + Delay_mat;
    end
end