import os, uuid, subprocess, threading
from pathlib import Path
from flask import Flask, request, send_file
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import requests as req

app = Flask(__name__)
TWILIO_ACCOUNT_SID  = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUM = os.environ.get("TWILIO_WHATSAPP_NUM", "whatsapp:+14155238886")
OUTPUT_DIR = Path("/tmp/vocal_remover")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(msg, flush=True)

def send_whatsapp(to, body):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(from_=TWILIO_WHATSAPP_NUM, to=to, body=body)

def remove_vocals(input_path, output_path):
    cmd = ["ffmpeg", "-y", "-i", str(input_path),
           "-af", "pan=mono|c0=c0-c1",
           "-ar", "44100", "-ab", "64k",
           str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-500:]}")

def upload_to_filebin(file_path, bin_id):
    log(f"Uploading to filebin.net, bin={bin_id}")
    file_bytes = open(file_path, 'rb').read()
    log(f"File size: {len(file_bytes)} bytes")
    r = req.post(
        f'https://filebin.net/{bin_id}/instrumental.mp3',
        data=file_bytes,
        headers={'Content-Type': 'audio/mpeg', 'accept': 'application/json'},
        timeout=90
    )
    log(f"filebin.net response: {r.status_code}")
    if r.status_code in (200, 201):
        return f'https://filebin.net/{bin_id}/instrumental.mp3'
    raise RuntimeError(f"filebin upload failed: {r.status_code}")

def process_job(media_url, from_number, job_id):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_file  = job_dir / "input.mp3"
    output_file = job_dir / "output.mp3"
    try:
        log(f"Downloading media for job {job_id}")
        resp = req.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=60)
        resp.raise_for_status()
        input_file.write_bytes(resp.content)
        log(f"Downloaded {len(resp.content)} bytes, running ffmpeg")
        remove_vocals(input_file, output_file)
        log("ffmpeg done, uploading")
        bin_id = job_id[:12]
        download_url = upload_to_filebin(output_file, bin_id)
        log(f"Upload done: {download_url}")
        send_whatsapp(from_number,
            f"Done! Your vocal-removed track is ready.\n\n"
            f"Tap the link below to download and play it:\n"
            f"{download_url}\n\n"
            f"(Link works for 6 days)"
        )
        log("Message sent successfully")
    except Exception as exc:
        log(f"Error in process_job: {exc}")
        send_whatsapp(from_number, f"Sorry, something went wrong: {exc}\n\nPlease try again.")
    finally:
        if input_file.exists(): input_file.unlink()
        if output_file.exists(): output_file.unlink()

@app.route("/webhook", methods=["POST"])
def webhook():
    num_media = int(request.values.get("NumMedia", 0))
    from_number = request.values.get("From", "")
    body = request.values.get("Body", "").strip().lower()
    resp = MessagingResponse()
    if num_media == 0:
        if body in ("hi", "hello", "hey", "start", "help"):
            resp.message("Hi! I am your Vocal Remover Bot.\n\nSend me any audio file and I will remove the vocals. You will get a download link back within 1-2 minutes!")
        else:
            resp.message("Send me an audio file (MP3, OGG, M4A, WAV) and I will remove the vocals!")
        return str(resp)
    media_url = request.values.get("MediaUrl0")
    job_id = str(uuid.uuid4())
    threading.Thread(target=process_job, args=(media_url, from_number, job_id), daemon=True).start()
    resp.message("Got it! Removing vocals now...\nI will send you a download link in about 1-2 minutes!")
    return str(resp)

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
