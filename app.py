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
BASE_URL            = os.environ.get("BASE_URL", "").rstrip("/")
OUTPUT_DIR = Path("/tmp/vocal_remover")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def send_whatsapp(to, body, media_url=None):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    kwargs = dict(from_=TWILIO_WHATSAPP_NUM, to=to, body=body)
    if media_url:
        kwargs["media_url"] = [media_url]
    client.messages.create(**kwargs)

def remove_vocals(input_path, output_path):
    cmd = ["ffmpeg", "-y", "-i", str(input_path),
           "-af", "pan=stereo|c0=c0-c1|c1=c1-c0",
           "-ar", "44100", "-ab", "192k", str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr}")

def upload_to_public_host(file_path):
    """Upload to 0x0.st for reliable public hosting that WhatsApp can access."""
    with open(file_path, 'rb') as f:
        resp = req.post(
            'https://0x0.st',
            files={'file': ('no_vocals.mp3', f, 'audio/mpeg')},
            timeout=120
        )
    if resp.status_code == 200:
        return resp.text.strip()
    raise RuntimeError(f"Upload failed with status {resp.status_code}")

def process_job(media_url, from_number, job_id):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_file  = job_dir / "input.mp3"
    output_file = job_dir / "no_vocals.mp3"
    try:
        resp = req.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=60)
        resp.raise_for_status()
        input_file.write_bytes(resp.content)
        remove_vocals(input_file, output_file)
        file_url = upload_to_public_host(output_file)
        send_whatsapp(from_number, "Done! Here is your vocal-removed track:", media_url=file_url)
    except Exception as exc:
        send_whatsapp(from_number, f"Sorry, something went wrong: {exc}\n\nPlease try again or send the file in MP3 format.")
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
            resp.message("Hi! I am your Vocal Remover Bot.\n\nSimply send me an audio file (MP3, OGG, M4A) and I will strip the vocals and send back the instrumental version - usually within 1-2 minutes!")
        else:
            resp.message("Send me an audio file and I will remove the vocals for you!\n\nSupported formats: MP3, OGG, M4A, WAV")
        return str(resp)
    media_url = request.values.get("MediaUrl0")
    job_id = str(uuid.uuid4())
    threading.Thread(target=process_job, args=(media_url, from_number, job_id), daemon=True).start()
    resp.message("Got it! Removing vocals now...\nThis usually takes 1-2 minutes. I will send the result straight back to you!")
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
