# Isolated Image Tools Worker

This worker keeps `rembg` and related image-tool dependencies out of the Odysseus core venv.

Current MVP capability:

- `POST /remove-background`

It accepts JSON with:

- `image_base64`
- optional `hint_mask_base64`

It returns JSON with either:

- `image_base64` containing PNG output
- or a structured error with `error_code` and `message`

## Why this exists

- Odysseus core must still start without `rembg`, `PIL`, `transformers`, or worker dependencies.
- Background removal is an optional worker capability, not a core dependency.
- A missing worker or missing worker dependencies should fail clearly and locally, not crash the core.

## Python target

Recommended local worker runtime:

- Python `3.12`

The core venv does not need these packages.

## Local Windows setup

```powershell
cd C:\Users\nkatz\odysseus\workers\image_tools_worker
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Start the worker

```powershell
cd C:\Users\nkatz\odysseus\workers\image_tools_worker
.venv\Scripts\Activate.ps1
$env:IMAGE_TOOLS_WORKER_HOST = "127.0.0.1"
$env:IMAGE_TOOLS_WORKER_PORT = "8123"
$env:IMAGE_TOOLS_WORKER_MAX_MB = "10"
python app.py
```

Default bind:

- host: `127.0.0.1`
- port: `8123`

## Worker request shape

Example request:

```json
{
  "image_base64": "<base64 image bytes>",
  "hint_mask_base64": "<optional base64 mask bytes>"
}
```

Example success:

```json
{
  "image_base64": "<base64 png bytes>",
  "mime_type": "image/png",
  "hint_mask_accepted": true,
  "hint_mask_applied": false
}
```

Example error:

```json
{
  "error_code": "dependency_missing",
  "message": "rembg is not installed in the image tools worker environment."
}
```

## Error semantics

The worker uses structured error codes aligned with the core client contract:

- `not_configured`
- `dependency_missing`
- `worker_unreachable`
- `timeout`
- `invalid_image`
- `payload_too_large`
- `permission_denied`

This MVP mainly emits:

- `dependency_missing`
- `invalid_image`
- `payload_too_large`

## Limits and safety

- Only `POST /remove-background` is exposed.
- Input must be JSON.
- Base64 must decode cleanly.
- Input payload is limited by `IMAGE_TOOLS_WORKER_MAX_MB`.
- Output is validated as PNG before returning.
- The worker prefers localhost and does not assume remote exposure.

## Hint mask behavior

`hint_mask_base64` is accepted for forward compatibility with the core client and future route integration.

Current MVP behavior:

- payload is accepted and validated
- the mask is not yet applied to `rembg`
- the response reports `hint_mask_accepted` and `hint_mask_applied`

This keeps the wire contract stable without pulling route logic into the worker slice.

## Session reuse

The worker lazily creates one `rembg` session per process and reuses it for requests.

If that ever causes environment-specific issues, replace it in a later slice with a stricter lifecycle strategy.

## Docker

Docker is intentionally not implemented in this slice, but the worker layout is compatible with a later container wrapper.

## Smoke check

This slice is validated with:

```powershell
python -m py_compile workers\image_tools_worker\app.py
python -m pytest tests\test_image_tools_worker_mvp_static.py
```
