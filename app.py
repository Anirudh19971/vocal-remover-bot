import os, uuid, subprocess, threading, re
from pathlib import Path
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import requests as req

app = Flash(__name__)
TWILIO_ACCOUNT_SID  = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUM = os.environ.get("TWILIO_WHATSAPP_NUM", "whatsapp:+14155238886")
OUTPUT_DIR = Path("/tmp/vocal_remover")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

YOUTUBE_RE = re.compile(r'(https?://)?(www\.)?(youtube\.com/watch\?[^\s]*v=[\w-]+|youtu\.be/[\w-]+)')

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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-500:]}")

def upload_to_filebin(file_path, bin_id):
    log(f"Uploading to filebin.net bin={bin_id}")
    data = open(file_path, 'rb').read()
    log(f"File size: {len(data)} bytes")
    r = req.post(f'https://filebin.net/{bin_id}/instrumental.mp3',
                 data=data, headers={'Content-Type': 'audio/mpeg'}, timeout=120)
    log(f"filebin response: {r.status_code}")
    if r.status_code in (200, 201):
        return f'https://filebin.net/{bin_id}/instrumental.mp3'
    raise RuntimeError(f"filebin failed: {r.status_code}")

def download_youtube(url, output_path):
    import yt_dlp
    temp_base = str(output_path.parent / 'yt_dl')
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': temp_base,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}],
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        duration = info.get('duration', 0)
        title = info.get('title', 'Unknown')
        if duration > 600:
            raise RuntimeError("Video is over 10 minutes. Please send a shorter clip.")
    downloaded = Path(temp_base + '.mp3')
    if downloaded.exists():
        downloaded.rename(output_path)
    return title

def process_audio_job(input_path, from_number, job_id, title=None):
    output_file = input_path.parent / "output.mp3"
    try:
        log(f"Running ffmpeg for job {job_id}")
        remove_vocals(input_path, output_file)
        log("ffmpeg done, uploading")
        bin_id = job_id[:12]
        download_url = upload_to_filebin(output_file, bin_id)
        log(f"Upload done: {download_url}")
        label = f'"{title}"\n\n' if title else ''
        send_whatsapp(from_number,
            f"Done! {label}Tap the link to download your vocal-removed track:\n"
            f"{download_url}\n\n(Link works for 6 days)"
        )
        log("Message sent")
    except Exception as exc:
        log(f"Error: {exc}")
        send_whatsapp(from_number, f"Sorry, something went wrong: {exc}\n\nPlease try again.")
    finally:
        if input_path.exists(): input_path.unlink()
        if output_file.exists(): output_file.unlink()

def process_file_job(media_url, from_number, job_id):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_file = job_dir / "input.mp3"
    try:
        log(f"Downloading Twilio media for job {job_id}")
        resp = req.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=60)
        resp.raise_for_status()
        input_file.write_bytes(resp.content)
        log(f"Downloaded {len(resp.content)} bytes")
        process_audio_job(input_file, from_number, job_id)
    except Exception as exc:
        log(f"File job error: {exc}")
        send_whatsapp(from_number, f"Sorry, something went wrong: {exc}\n\nPlease try again.")

def process_youtube_job(url, from_number, job_id):
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_file = job_dir / "input.mp3"
    try:
        log(f"Downloading YouTube: {url}")
        title = download_youtube(url, input_file)
        log(f"Downloaded: {title}")
        process_audio_job(input_file, from_number, job_id, title=title)
    except Exception as exc:
        log(f"YouTube job error: {exc}")
        send_whatsapp(from_number, f"Sorry, could not process that link: {exc}\n\nPlease try again.")

@app.route("/webhook", methods=["POST"])
def webhook():
    num_media = int(request.values.get("NumMedia", 0))
    from_number = request.values.get("From", "")
    body = request.values.get("Body", "").strip()
    resp = MessagingResponse()

    if num_media == 0:
        yt_match = YOUTUBE_RE.search(body)
        if yt_match:
            url = yt_match.group(0)
            if not url.startswith('http'):
                url = 'https://' + url
            job_id = str(uuid.uuid4())
            threading.Thread(target=process_youtube_job, args=(url, from_number, job_id), daemon=True).start()
            resp.message("Got it! Downloading from YouTube and removing vocals...\nThis takes 2-4 minutes. I will send you a download link!")
        elif body.lower() in ("hi", "hello", "hey", "start", "help"):
            resp.message("Hi! I am your Vocal Remover Bot.\n\nSend me:\n- An audio file (MP3, OGG, M4A, WAV)\n- A YouTube link\n\nI will strip the vocals and send you a download link!")
        else:
            resp.message("Send me an audio file or a YouTube link and I will remove the vocals!")
        return str(resp)

    media_url = request.values.get("MediaUrl0")
    job_id = str(uuid.uuid4())
    threading.Thread(target=process_file_job, args=(media_url, from_number, job_id), daemon=True).start()
    resp.message("Got it! Removing vocals now...\nI will send you a download link in about 1-2 minutes!")
    return str(resp)

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
