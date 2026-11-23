# NeuroHelen Studio — source code for review

Public source snapshot of the AI cartoon studio: https://studio.neurohelen.com/

Python backend, SQLite, JavaScript/HTML/CSS frontend, FFmpeg, and integrations with OpenAI, Google Cloud Vertex AI, BytePlus, Firebase, Telegram, Stripe, YooKassa and Resend.

## Run a local demo

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m webstudio.server --port 8090
```

Open http://127.0.0.1:8090/studio. Demo generation is simulated. Live generation requires separately configured provider accounts and credentials.

## Structure

- `webstudio/`: browser UI, authentication, payments, support and public pages.
- `cartoonbot/`: shared screenplay, image and video generation workflow.
- `tests/`: automated checks.

This is an independent snapshot for review, with no original Git history. Credentials, runtime data, customer projects, production deployment settings and example videos are omitted. Some marketing example previews are therefore unavailable in this copy. The production repository remains private.

Source shared for inspection; no open-source licence is granted. All rights reserved.
