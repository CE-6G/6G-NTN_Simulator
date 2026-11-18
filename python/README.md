## A) Run back-end server

On windows:
```powershell
python python/api_server.py --host 127.0.0.1 --port 8000
```

On Mac:
```bash
python python/api_server.py --host 127.0.0.1 --port 8000
```



Health check:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
```

On Mac:
```bash
curl http://127.0.0.1:8000/api/health
```

## B) Run front-end HTTP server
In another terminal, activate the venv and run the HTTP server
```powershell
python -m http.server 8080
```

On Mac (from the project folder):
```bash
source .venv/bin/activate
python -m http.server 8080
```


## C) Open frontend and run a simulation

Open file in browser:
http://127.0.0.1:8080/visualizer/index.html

In the panel:
- Ensure API host/port are `127.0.0.1:8000`.
- Click `Start Backend`, and wait for the `MATLAB engine started/connected: matlab_connected` message to be displayed in the `Status` tab.
- Select the simulation parameters in the `Connection`, `Flow Setup` and `Network Setup` tabs.
- Click `Init` in the `Connection` tab.
- Check the results of the simulation in the `Simulation Results` tab.
