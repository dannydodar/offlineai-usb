# OfflineAI USB Assistant

OfflineAI is a portable Windows web interface for a local assistant backed by
the PDF library on the same USB drive. The packaged launcher uses an
independent CPU build of `llama.cpp`; LM Studio is not required for the USB
package.

## Package layout

The deployable folder is normally `AI\\OfflineAI` on the USB drive:

- `models\\` contains the local GGUF models.
- `runtime\\` contains the portable Python runtime and `llama.cpp` runner.
- `worker-pdf\\pdf_catalog.sqlite3` contains the private PDF catalog.
- `..\\..\\PDF\\` is the PDF library and its compact index files.
- `app\\` and `ui\\` are the software updated from this repository.

Models, PDFs, the catalog database, and runtime binaries are intentionally not
stored in the public GitHub repository. The update installer preserves them.
The updater carries its own Mozilla CA certificate bundle so GitHub HTTPS
updates continue to work with the portable Python runtime on another laptop.
Certificate verification remains enabled.

On computers with around 6 GB of RAM or less, or with a low-power four-core
CPU, the launcher automatically enters low-resource mode. It reduces the
llama.cpp context allocation, keeps PDF retrieval small, and uses the optional
Qwen3 0.6B model when that model is present. Copy
`models\qwen3-0.6b\Qwen3-0.6B-Q4_0.gguf` into the package to make that fallback
available; the model is not stored in this public repository.
If it is missing, open Settings in OfflineAI and use **Download super-light
model**. The downloader shows progress, retries interrupted transfers, and
verifies the model before making it available.
After installing any update, use the **Restart OfflineAI** button in Settings
to relaunch the portable stack and load the new files.
The Models section in Settings shows the active model, cache location, and
file sizes, and lets you delete inactive cached models safely.

## Running the USB package

From `AI\\OfflineAI`, double-click `start_usb.cmd`. It starts the independent
CPU inference server and the OfflineAI backend, then opens the local web UI.
On first start it copies the selected model set to
`%LOCALAPPDATA%\\OfflineAI\\models` and uses that PC copy for inference. This
avoids repeatedly reading multi-gigabyte model files from the USB. The PDFs
and catalog continue to be read from the USB.

The default model is E2B. To use E4B, run the PowerShell launcher explicitly:

```powershell
.\\start_usb.ps1 -Model e4b
```

Use `offload_models.cmd` to prepare a model without starting the assistant.
Use `start_usb.cmd -NoOffload` only when you deliberately want to run the
model directly from the USB.

Use `stop_usb.cmd` to stop only processes started by the USB launcher. The
launcher is deliberately localhost-only and has no LAN mode. If the default
web port `8765` is already occupied, it automatically uses the first free
fallback port from `8775` through `8790` and opens that URL.

At the root of the USB, `Start OfflineAI.cmd` is a self-locating shortcut that
finds the `AI\\OfflineAI` folder regardless of which drive letter Windows
assigns to the USB.

## Local development

`start_independent_stack.cmd` runs the E2B model through the independent
`llama.cpp` CPU runner and starts the backend on localhost. The development
launcher expects the model and PDF paths used on the build machine; the USB
launcher uses paths relative to the USB drive instead.

The UI settings panel exposes model selection, retrieval limit, context window
and response budget, temperature, Top P, the active system prompt, engine
status, and update controls. Greetings and ordinary conversation do not query
the PDF library. Retrieval is reserved for substantive questions and is kept
within the selected source and context budgets.

## Software updates

The Settings panel's **Check for updates** button checks the public repository
configured in `update_config.json`. If a newer version is available, **Install
update** downloads the GitHub branch archive and updates only software files.
It creates a timestamped backup and preserves models, PDFs, the catalog,
runtime, and logs. Restart the USB launcher after installing an update.

The public software repository is:

`https://github.com/dannydodar/offlineai-usb`

## Rebuilding the PDF catalog

If the PDF library changes, rebuild the catalog with the local indexing worker
before copying the updated database to the USB package. The PDF files and
catalog remain local/private and are not part of the public repository.
