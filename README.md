# Python-Arena

Python-Arena is a multiplayer Python/Pygame project with a backend server and one or more game clients.

## Requirements

- Python 3.11 or newer recommended
- Project dependencies from `requirements.txt`

Install dependencies from the project root:

```powershell
python -m pip install -r requirements.txt
```

## Run The Server

Open a terminal in the project root:

```powershell
cd "C:\Users\rudya\OneDrive - American University of Beirut\Python-Arena"
```

Start the backend server on port `6100`:

```powershell
python -m server.server 6100
```

The server will listen for clients on that port. The port does not have to be `6100`; it can be any available TCP port, such as `5000`, `6100`, or `7000`.

## Run A Client

Open a second terminal in the project root:

```powershell
cd "C:\Users\rudya\OneDrive - American University of Beirut\Python-Arena"
```

Start a client and connect it to the local server:

```powershell
python -m client.client --prelobby --server-ip 127.0.0.1 --server-port 6100
```

Use this command for each client you want to open. For local testing, keep `127.0.0.1` because the server is running on the same computer.

## Quick Local Test

Use two terminals:

Terminal 1:

```powershell
python -m server.server 6100
```

Terminal 2:

```powershell
python -m client.client --prelobby --server-ip 127.0.0.1 --server-port 6100
```

To test multiplayer on the same machine, open more client terminals and run the same client command again.

## Command Notes

- `6100` is only an example TCP port. You can use any available port.
- `--server-ip 127.0.0.1` means the client connects to a server running on the same computer.
- `--server-port 6100` must match the port you used when starting the server.
- `--prelobby` opens the pre-lobby/lobby flow before entering gameplay.

For example, this also works if port `7000` is available:

Terminal 1:

```powershell
python -m server.server 7000
```

Terminal 2:

```powershell
python -m client.client --prelobby --server-ip 127.0.0.1 --server-port 7000
```

## LAN Testing

If another computer on the same network needs to connect:

1. Run the server on the host computer:

```powershell
python -m server.server 6100
```

2. On the other computer, replace `127.0.0.1` with the host computer's local IP address:

```powershell
python -m client.client --prelobby --server-ip HOST_COMPUTER_IP --server-port 6100
```

Example:

```powershell
python -m client.client --prelobby --server-ip 192.168.1.25 --server-port 6100
```
