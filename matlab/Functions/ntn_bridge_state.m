function state = ntn_bridge_state(action, newState)
%NTN_BRIDGE_STATE Manage shared persistent simulation state across MATLAB functions.
%   STATE = NTN_BRIDGE_STATE() retrieves the current persistent simulation
%   state structure, initializing it with default parameters on first call.
%
%   STATE = NTN_BRIDGE_STATE(ACTION) executes the specified state management
%   command ("get" or "reset") and returns the resulting state structure.
%
%   STATE = NTN_BRIDGE_STATE("set", NEWSTATE) overwrites the persistent
%   simulation state with NEWSTATE and returns the updated structure.
%
%   Syntax:
%       state = ntn_bridge_state()
%       state = ntn_bridge_state("get")
%       state = ntn_bridge_state("set", newState)
%       state = ntn_bridge_state("reset")
%
%   Inputs:
%       action   - (Optional) Command string or character vector specifying
%                  the state operation (default: "get"):
%                    * "get"   - Retrieve the current simulation state.
%                    * "set"   - Replace the stored state with newState.
%                    * "reset" - Reinitialize the stored state to defaults.
%       newState - State structure required when action is "set".
%
%   Outputs:
%       state    - Scalar struct holding the active NTN simulation state,
%                  including scenario handles, orbital/RF parameters, and
%                  simulation timing metadata.
%
%   Notes:
%       Uses a persistent workspace variable so multiple MATLAB bridge
%       functions can access and update shared scenario objects and RF
%       configuration parameters without passing global state explicitly.
%
%   See also PERSISTENT, STRUCT, NAT.

    %% Persistent State Initialization
    persistent bridgeState

    % Default to "get" when no action argument is supplied
    if nargin < 1 || isempty(action)
        action = "get";
    end

    % Populate persistent storage with default configuration on initial call
    if isempty(bridgeState)
        bridgeState = default_state();
    end

    %% Command Dispatch
    switch lower(string(action))
        case "get"
            % Return current persistent simulation state
            state = bridgeState;
        case "set"
            % Validate and overwrite persistent state with caller-supplied struct
            if nargin < 2
                error("ntn_bridge_state:set", "newState is required");
            end
            bridgeState = newState;
            state = bridgeState;
        case "reset"
            % Restore persistent state to factory default values
            bridgeState = default_state();
            state = bridgeState;
        otherwise
            error("ntn_bridge_state:action", "Unsupported action: %s", string(action));
    end
end

%% Local Helper Functions

function state = default_state()
%DEFAULT_STATE Construct the default NTN simulation bridge state structure.
%   STATE = DEFAULT_STATE() returns an uninitialized state struct populated
%   with baseline Ka-band RF parameters and empty scenario object placeholders.
    state = struct( ...
        "initialized", false, ...
        "tleFile", "", ...
        "simulationStartTime", NaT, ...
        "startTime", NaT, ...
        "stopTime", NaT, ...
        "sampleTime", 30, ...
        "scenario", [], ...
        "satellites", [], ...
        "groundStations", [], ...
        "frequencyGHz", 28, ...
        "txPowerW", 20, ...
        "bitRateMbps", 100, ...
        "systemLossDb", 3, ...
        "linkValidBaseSeconds", 20);
end