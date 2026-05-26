import os, uuid, subprocess, threading, sys
from pathlib import Path
from flask import Flask, request, send_file
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import requests as req

app = Flask(__name__)
TWILIO_ACCOUNT_SID  = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUM = os.environ.get("TWILIO_WHATSAPP_NUM", "whatsapp:+14155238886")
BASE_URL            = os.environ.get("BASE_URL", "").rstrip("/")
OUTPUT_DIR = Path("/tmp/vocal_remover")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(msg, flush=True)

def send_whatsapp(to, body, media_url=None):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    kwargs = dict(from_=TWILIO_WHATSAPP_NUM, to=to, body=body)
    if media_url:
        kwargs["media_url"] = [media_url]
    client.messages.create(**kwargs)

def remove_vocals(input_path, output_path):
    # Mono MP3 64kbps - much smaller file for faster upload
    cmd = ["ffmpeg", "-y", "-i", str(input_path),
           "-af", "pan=mono|c0=c0-c1",
           "-ar", "44100", "-ab", "64k",
           str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-500:]}")

def try_upload(file_path, job_id):
    """Try filebin.net upload. Returns URL or None."""
    try:
        bin_id = job_id[:12]
        log(f"Uploading to filebin.net, bin={bin_id}")
        file_bytes = open(file_path, 'rb').read()
        log(f"File size: {len(file_bytes)} bytes")
        r = req.post(
            f'https://filebin.net/{bin_id}/instrumental.mp3',
            data=file_bytes,
            headers={'Content-Type': 'audio/mpeg', 'accept': 'application/json'},
            timeout=60
        )
        log(f"filebin.net response: {r.status_code}")
        if r.status_code in (200, 201):
            return f'https://filebin.net/{bin_id}/instrumental.mp3'
    except Exception as e:
        log(f"filebin upload error: {e}")
    return None

def process_job(media_url, from_number, job_id):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_file  = job_dir / "input.mp3"
    output_file = job_dir / "output.mp3"
    try:
        log(f"Downloading media from Twilio for job {job_id}")
        resp = req.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=60)
        resp.raise_for_status()
        input_file.write_bytes(resp.content)
        log(f"Downloaded {len(resp.content)} bytes, running ffmpeg")
        remove_vocals(input_file, output_file)
        log("ffmpeg done, attempting upload")

        public_url = try_upload(output_file, job_id)

        if public_url:
            log(f"Upload succeeded: {public_url}")
            try:
                send_whatsapp(from_number, "Done! Here is your vocal-removed track:", media_url=public_url)
                log("WhatsApp media message sent")
            except Exception as e:
                log(f"Media send failed ({e}), sending link instead")
                send_whatsapp(from_number, f"Done! Tap to download your vocal-removed track:\n{public_url}")
        else:
            # Fall back: serve from our own URL and send as a download link
            log("Upload failed, using Railway URL as download link")
            dl_url = f"{BASE_URL}/audio/{job_id}/output.mp3"
            send_whatsapp(from_number, f"Done! Tap the link below to download your vocal-removed track (works in browser):\n{dl_url}")

    except Exception as exc:
        log(f"process_job error: {exc}")
        send_whatsapp(from_number, f"Sorry, something went wrong: {exc}\n\nPlease try again or send the file in MP3 format.")
    finally:
        if input_file.exists(): input_file.unlink()

@app.route("/webhook", methods=["POST"])
def webhook():
    num_media = int(request.values.get("NumMedia", 0))
    from_number = request.values.get("From", "")
    body = request.values.get("Body", "").strip().lower()
    resp = MessagingResponse()
    if num_media == 0:
        if body in ("hi", "hello", "hey", "start", "help"):
            resp.message("Hi! I am your Vocal Remover Bot.\n\nSend me any audio file (MP3, OGG, M4A, WAV) and I will strip the vocals and send back the instrumental!")
        else:
            resp.message("Send me an audio file and I will remove the vocals!\n\nSupported: MP3, OGG, M4A, WAV")
        return str(resp)
    media_url = request.values.get("MediaUrl0")
    job_id = str(uuid.uuid4())
    threading.Thread(target=process_job, args=(media_url, from_number, job_id), daemon=True).start()
    resp.message("Got it! Removing vocals now...\nThis usually takes 1-2 minutes. I will send the result straight back!")
    return str(resp)

@app.route("/audio/<job_id>/<filename>")
def serve_audio(job_id, filename):
    file_path = OUTPUT_DIR / job_id / filename
    if file_path.exists():
        return send_file(str(file_path), mimetype="audio/mpeg")
    return "File not found", 404

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
