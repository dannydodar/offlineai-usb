# OfflineAI on antiX/Linux

This package is a local-only offline assistant for small laptops. It does not
use LM Studio, does not listen on the network, and does not require an internet
connection after the package and model have been copied to the laptop.

## First run

Open a terminal in this folder and run:

```sh
bash ./start_offlineai.sh
```

After the first successful run, you can create a one-click desktop shortcut:

```sh
bash ./install_offlineai_desktop.sh
```

The first run copies the Qwen3 0.6B model and the CPU runner into the laptop's
user cache. That avoids repeatedly reading the large files from the USB. The
browser opens at `http://127.0.0.1:8765/`.

To stop it:

```sh
bash ./stop_offlineai.sh
```

The application is deliberately conservative for a 4 GB Celeron laptop:
Qwen3 0.6B, CPU-only inference, one request at a time, and a 2,048-token
context window. PDF retrieval remains off unless the question explicitly asks
for PDFs, books, manuals, documents, sources, or the local library.

The backend and runner logs are stored in the user's local state directory:
`~/.local/state/offlineai/logs/`.
