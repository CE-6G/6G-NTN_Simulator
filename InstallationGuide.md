# Installation Guide of the 6G-NTN simulator

## Windows
1. ### Clone the project
```bash
git clone git@github.com:CE-6G/6G-NTN_Simulator.git
```
This uses SSH, which requires an SSH key registered on your GitHub account ([GitHub guide](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)). If you don't have one, clone over HTTPS instead:
```bash
git clone https://github.com/CE-6G/6G-NTN_Simulator.git
```

Then move into the project folder (all the following commands are run from there):
```bash
cd 6G-NTN_Simulator
```

2. ### Check MATLAB & Python version compatibility
Check your MATLAB version. Then, edit and save the `requirements.txt` file, replacing the `matlabengine==24.2.*` line with the `matlabengine` version that matches your release in the following table:

| MATLAB Release | `matlabengine` Package (`pip`) | Compatible Python Versions |
| :--- | :--- | :--- |
| **R2026b** | `matlabengine==26.2.*` | 3.11, 3.12, 3.13, 3.14 |
| **R2026a** | `matlabengine==26.1.*` | 3.11, 3.12, 3.13 |
| **R2025b** | `matlabengine==25.2.*` | 3.11, 3.12 |
| **R2025a** | `matlabengine==25.1.*` | 3.11, 3.12 |
| **R2024b** | `matlabengine==24.2.*` | 3.11, 3.12 |
| **R2024a** | `matlabengine==24.1.*` | 3.11 |

*(The Python versions listed are those supported by both MATLAB and this project: the simulator's dependencies require Python 3.11 or newer.)*


3. ### Create a virtual environment
Create a virtual environment with the one of the Python versions compatible with your MATLAB Release (see table above).
For example, with MATLAB R2024b, python3.12 can be used:
```bash
py -3.12 -m venv .venv
```

Then, activate your venv:
```bash
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script with a "running scripts is disabled on this system" error, run this command once to allow local scripts for your user account, then try activating again:
```bash
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

4. ### Install the requirements
Install the requirements from the `requirements.txt` file:
```bash
pip install -r requirements.txt
```

5. ### Run the simulator
Launch MATLAB on your device and refer to the .\python\README.md file for how to launch the simulator.

## macOS / Linux
1. ### Clone the project
```bash
git clone git@github.com:CE-6G/6G-NTN_Simulator.git
```
This uses SSH, which requires an SSH key registered on your GitHub account ([GitHub guide](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)). If you don't have one, clone over HTTPS instead:
```bash
git clone https://github.com/CE-6G/6G-NTN_Simulator.git
```

Then move into the project folder (all the following commands are run from there):
```bash
cd 6G-NTN_Simulator
```

2. ### Check MATLAB & Python version compatibility
Check your MATLAB version. Then, edit and save the `requirements.txt` file, replacing the `matlabengine==24.2.*` line with the `matlabengine` version that matches your release in the following table:

| MATLAB Release | `matlabengine` Package (`pip`) | Compatible Python Versions |
| :--- | :--- | :--- |
| **R2026b** | `matlabengine==26.2.*` | 3.11, 3.12, 3.13, 3.14 |
| **R2026a** | `matlabengine==26.1.*` | 3.11, 3.12, 3.13 |
| **R2025b** | `matlabengine==25.2.*` | 3.11, 3.12 |
| **R2025a** | `matlabengine==25.1.*` | 3.11, 3.12 |
| **R2024b** | `matlabengine==24.2.*` | 3.11, 3.12 |
| **R2024a** | `matlabengine==24.1.*` | 3.11 |

*(The Python versions listed are those supported by both MATLAB and this project: the simulator's dependencies require Python 3.11 or newer.)*

3. ### Create a virtual environment
Create a virtual environment with one of the Python versions compatible with your MATLAB Release (see table above).
For example, with MATLAB R2024b, `python3.12` can be used:
```bash
python3.12 -m venv .venv
```

If `python3.12` is not found (most Macs only ship Apple's Python 3.9 at `/usr/bin/python3`, which is too old), install it first with [Homebrew](https://brew.sh) (`brew install python@3.12`) or from [python.org](https://www.python.org/downloads/macos/).

If a conda environment is active (e.g. `(base)` at the start of your prompt), run `conda deactivate` before creating the venv.

*(Note: On some Linux distributions, you may need to install the venv package first via `sudo apt install python3.12-venv`.)*

Then, activate your venv:
```bash
source .venv/bin/activate
```

4. ### Install the requirements
Install the requirements from the `requirements.txt` file:
```bash
pip install -r requirements.txt
```

Note for Apple Silicon (M1/M2/M3/M4) Macs: If `pip install matlabengine==...` fails or throws an architecture mismatch error (mach-o file, but is an incompatible architecture), install it directly from the local MATLAB app bundle instead:
```bash
pip install /Applications/MATLAB_R2024b.app/extern/engines/python
```
(Replace R2024b with your installed MATLAB release).

Check that the MATLAB engine package is installed correctly:
```bash
python -c "import matlab.engine; print('matlab.engine OK')"
```

5. ### Run the simulator
Launch MATLAB on your device and refer to the `./python/README.md` file for how to launch the simulator.
