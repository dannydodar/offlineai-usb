# OfflineAI USB Assistant

This is the local build directory for the portable offline assistant. The USB launcher runs the UI, PDF retrieval, and inference locally without LM Studio.

## Current status

- Local web interface and Python backend are integrated. Normal USB mode is localhost-only; the chat UI can be exposed to a private LAN only when deliberately launched with the LAN option.
- The backend uses the SQLite catalog produced by the PDF worker and the compact hub/category index under `E:\PDF\indexes`.
- LM Studio is expected at `http://127.0.0.1:1234/v1`.
- An independent CPU mode is now available using the bundled official `llama.cpp` `llama-server.exe` and the E2B GGUF. It runs on `127.0.0.1:1235` and does not require LM Studio.
- The USB build also includes a bundled Python runtime and can use models from its own `models` folder.
- The Settings panel contains model selection, library retrieval, context budget, generation controls, system prompt, and update controls.
- Default model: `google/gemma-4-e2b`.
- E4B can be selected from the interface.
- Only models configured in `app\model_registry.json` and found in the configured model folder are shown.
- Greetings and ordinary conversation skip library retrieval; substantive questions search the library.
- Parameters, the read-only system prompt, retrieval outcome, and estimated context usage are visible in the interface.
- The source PDFs remain in `E:\PDF` and are not modified except for the requested generated `index.md`.

## Start locally

Run `start_local.cmd`. It starts the backend and opens:

`http://127.0.0.1:8765/`

Run `stop_local.cmd` to stop the background server.

Run `start_independent_stack.cmd` to run the E2B model through the bundled independent CPU runner. Run `stop_independent_stack.cmd` to stop that runner and its backend. This is the current compatibility baseline for an eventual USB package; a GPU/Vulkan runner can be added after the laptop hardware is identified.

## USB launcher

Run `start_usb.cmd` from the USB package. It starts the independent llama.cpp runner, the OfflineAI backend, and the browser UI. `stop_usb.cmd` stops only the processes started by the USB launcher. The `-Lan` PowerShell option can be used deliberately when a private-network test is needed.

The update button checks the configured public GitHub repository and applies software-only updates. It preserves the local PDF database, PDFs, GGUF models, runtime, logs, and update backups.

## Temporary phone/LAN test

Run `start_lan.cmd` to start a separate temporary LAN listener on port `8766`. It prints the laptop's private-network URL and adds a Windows Firewall rule limited to the Private profile and LocalSubnet. On this laptop the current URL is:

`http://192.168.1.82:8766/`

The phone must be on the same Wi-Fi network. Run `stop_lan.cmd` when testing is finished; it stops only this LAN listener and removes its temporary firewall rule. The existing service on port `8765` is left untouched.

Run `rebuild_index.cmd` after adding or changing PDFs. The indexer is resumable and writes the catalog to `E:\PDF\index.md`.

The launcher uses a bundled runtime at `runtime\python.exe` when present, otherwise it uses the development Python runtime or `python` on PATH.

## Local test checklist

1. Confirm LM Studio is running its local server.
2. Start `start_local.cmd`.
3. Ask a question covered by the survival PDFs.
4. Expand the displayed sources and check the filename, path, and page number.
5. Switch between E2B and E4B.
6. Ask something not covered by the collection and confirm the assistant expresses uncertainty.
7. Stop the service with `stop_local.cmd`.

USB packaging is intentionally not done yet. It will happen only after local testing passes.
