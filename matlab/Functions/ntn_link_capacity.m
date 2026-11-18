function R = ntn_link_capacity(snr_db, linkType, varargin)
%NTN_LINK_CAPACITY Estimate achievable NTN link capacity using capped Shannon efficiency.
%   R = NTN_LINK_CAPACITY(SNR_DB, LINKTYPE) computes the net throughput R [bit/s]
%   for a Non-Terrestrial Network (NTN) link given signal-to-noise ratio SNR_DB [dB]
%   and link profile LINKTYPE ('D2D', 'ISL_RF', or 'ISL_OPT'), applying default
%   bandwidth, spectral efficiency ceilings, protocol overhead, and processing
%   efficiency factors for the selected link type.
%
%   R = NTN_LINK_CAPACITY(SNR_DB, LINKTYPE, Name, Value) overrides one or more
%   default physical-layer parameters using Name-Value pair arguments.
%
%   Syntax:
%       R = ntn_link_capacity(snr_db, linkType)
%       R = ntn_link_capacity(snr_db, linkType, Name, Value)
%
%   Inputs:
%       snr_db   - Signal-to-noise ratio in decibels [dB] (scalar or array).
%       linkType - Link architecture identifier (case-insensitive char/string):
%                    * 'D2D'     - Direct-to-Device ground-to-satellite access link
%                                  (defaults: BW = 10 MHz, eta_max = 4 bps/Hz,
%                                   Overhead = 0.20, ProcLoss = 0.85).
%                    * 'ISL_RF'  - RF Inter-Satellite Link
%                                  (defaults: BW = 150 MHz, eta_max = 8 bps/Hz,
%                                   Overhead = 0.10, ProcLoss = 0.93).
%                    * 'ISL_OPT' - Optical Inter-Satellite Link
%                                  (defaults: BW = 2 GHz, eta_max = 20 bps/Hz,
%                                   Overhead = 0.05, ProcLoss = 0.95).
%
%   Name-Value Arguments:
%       'ModulationEfficiency' - Maximum spectral efficiency ceiling [bit/s/Hz].
%                                Defaults to linkType profile value if empty.
%       'Overhead'             - Protocol/framing overhead fraction in [0, 1].
%                                Defaults to linkType profile value if empty.
%       'ProcLoss'             - Implementation/processing efficiency multiplier
%                                in [0, 1]. Defaults to linkType profile value.
%       'UseShannonCap'        - Logical flag (default: true). When true, spectral
%                                efficiency is min(log2(1 + SNR), eta_mod); when
%                                false, fixed efficiency eta_mod is used.
%       'Bandwidth'            - Channel bandwidth in hertz [Hz]. Defaults to
%                                linkType profile value if empty.
%
%   Outputs:
%       R        - Achievable net link bit rate [bit/s], matching the size of
%                  SNR_DB.
%
%   Notes:
%       Net throughput is evaluated as:
%           R = Bandwidth * eta * (1 - Overhead) * ProcLoss
%       Note that 'ProcLoss' acts as a multiplicative efficiency factor
%       (1 = lossless, < 1 = degraded throughput).
%
%   See also INPUTPARSER, LOG2.

    %% Parse Optional Name-Value Arguments
    p   = inputParser;
    addParameter(p,'ModulationEfficiency',[]);
    addParameter(p,'Overhead',[]);
    addParameter(p,'ProcLoss',[]);
    addParameter(p,'UseShannonCap',true);
    addParameter(p,'Bandwidth',[]); % Channel bandwidth [Hz]
    parse(p,varargin{:});
    opt = p.Results;
    
    %% Compute Unconstrained Shannon Spectral Efficiency
    snr_lin = 10.^(snr_db/10);        % Linear signal-to-noise ratio
    shannon_eta = log2(1 + snr_lin);  % Shannon-Hartley limit [bit/s/Hz]
    
    %% Apply Link-Type Physical Layer Defaults
    switch upper(linkType)
        case 'D2D' % Direct-to-Device (ground-to-satellite) link
            if isempty(opt.ModulationEfficiency), eta_mod = 4; else, eta_mod = opt.ModulationEfficiency; end
            if isempty(opt.Overhead), opt.Overhead = 0.2; end
            if isempty(opt.ProcLoss), opt.ProcLoss = 0.85; end
            if isempty(opt.Bandwidth), opt.Bandwidth = 10e6; end % 10 MHz bandwidth
        case 'ISL_RF' % Radio-Frequency Inter-Satellite Link
            if isempty(opt.ModulationEfficiency), eta_mod = 8; else, eta_mod = opt.ModulationEfficiency; end
            if isempty(opt.Overhead), opt.Overhead = 0.1; end
            if isempty(opt.ProcLoss), opt.ProcLoss = 0.93; end
            if isempty(opt.Bandwidth), opt.Bandwidth = 150e6; end % 150 MHz bandwidth
        case 'ISL_OPT' % Optical Inter-Satellite Link
            if isempty(opt.ModulationEfficiency), eta_mod = 20; else, eta_mod = opt.ModulationEfficiency; end
            if isempty(opt.Overhead), opt.Overhead = 0.05; end
            if isempty(opt.ProcLoss), opt.ProcLoss = 0.95; end
            if isempty(opt.Bandwidth), opt.Bandwidth = 2e9; end % 2 GHz bandwidth
        otherwise
            error('Unknown linkType.');
    end
    
    %% Evaluate Effective Spectral Efficiency and Net Bit Rate
    if opt.UseShannonCap
        % Cap Shannon capacity at the highest supported modulation order
        eta = min(shannon_eta, eta_mod);
    else
        % Use fixed peak modulation efficiency regardless of SNR
        eta = eta_mod .* ones(size(snr_lin));
    end
    
    % Scale raw bit rate by protocol overhead and processing efficiency
    R = opt.Bandwidth .* eta .* (1 - opt.Overhead) .* opt.ProcLoss;
end