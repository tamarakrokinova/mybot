import os
import json
import uuid
import uvicorn
import datetime
import random
from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

conversation_history = []
os.makedirs("/tmp/audio", exist_ok=True)

FILLERS = ["Mhm...", "Sure...", "Of course...", "Got it..."]

def get_calendar_service():
    token_json = os.getenv("GOOGLE_TOKEN")
    if token_json:
        creds_data = json.loads(token_json)
        creds = Credentials(
            token=creds_data.get("token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=creds_data.get("token_uri"),
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
            scopes=creds_data.get("scopes"),
        )
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        raise Exception("No credentials")
    return build("calendar", "v3", credentials=creds)

def tts(text):
    try:
        response = client.audio.speech.create(model="tts-1", voice="nova", input=text)
        filename = str(uuid.uuid4()) + ".mp3"
        filepath = "/tmp/audio/" + filename
        with open(filepath, "wb") as f:
            f.write(response.content)
        return BASE_URL + "/audio/" + filename
    except Exception as e:
        print("TTS ERROR:", e)
        return None

def xml_play(url, next_action="/handle-speech", end=False):
    if end:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Play>' + url + '</Play></Response>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Play>' + url + '</Play>'
        '<Gather input="speech" action="' + next_action + '" language="en-US" timeout="10" speechTimeout="3"></Gather>'
        '</Response>'
    )

def xml_say(text, end=False):
    if end:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice">' + text + '</Say></Response>'
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice">' + text + '</Say><Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="3"></Gather></Response>'

app = FastAPI()

@app.get("/audio/{filename}")
async def get_audio(filename: str):
    return FileResponse("/tmp/audio/" + filename, media_type="audio/mpeg")

@app.post("/incoming-call")
async def incoming_call(request: Request):
    global conversation_history
    conversation_history = []
    print("=== NEW CALL ===")
    url = tts("Hello! Thank you for calling Union Recording Studio. How can I help you today?")
    if url:
        return Response(content=xml_play(url), media_type="application/xml")
    return Response(content=xml_say("Hello! Thank you for calling Union Recording Studio. How can I help you?"), media_type="application/xml")

@app.post("/handle-speech")
async def handle_speech_alias(request: Request):
    return await filler(request)

@app.post("/filler")
async def filler(request: Request):
    form = await request.form()
    user_said = form.get("SpeechResult", "")
    print("PATIENT:", user_said)
    conversation_history.append({"role": "user", "content": user_said})
    filler_text = random.choice(FILLERS)
    url = tts(filler_text)
    if url:
        return Response(content=xml_play(url, next_action="/respond"), media_type="application/xml")
    return Response(content=xml_say(filler_text, next_action="/respond"), media_type="application/xml")

@app.post("/respond")
async def respond(request: Request):
    today = datetime.date.today().strftime("%Y-%m-%d")
    year = datetime.date.today().year
    prompt = (
        "You are a friendly receptionist at Union Recording Studio. "
        "Locations: Rampart and Santa Monica. "
        "Today is " + today + ". Year is " + str(year) + ". "
        "Help clients book recording sessions. Ask location if not mentioned. Never ask year. "
        "Convert times to 24-hour. When you have location+date+time output ONLY: BOOK: LOCATION YYYY-MM-DD HH:MM "
        "After booking ask: Is there anything else I can help you with? "
        "If done output ONLY: GOODBYE"
    )

    result = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": prompt}] + conversation_history,
    )
    answer = result.choices[0].message.content.strip()
    print("BOT:", answer)
    conversation_history.append({"role": "assistant", "content": answer})

    if "BOOK:" in answer:
        try:
            book_line = answer.split("BOOK:")[1].split("\n")[0].strip()
            parts = book_line.split()
            location = parts[0] + " " + parts[1] if len(parts) == 4 else parts[0]
            date = parts[-2]
            time = parts[-1]
            service = get_calendar_service()
            event = {
                "summary": "Recording Session - " + location,
                "start": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
                "end": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
            }
            service.events().insert(calendarId="primary", body=event).execute()
            dt = datetime.datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
            friendly = dt.strftime("%B %d at %I:%M %p")
            spoken = "Perfect! Your session at " + location + " is booked for " + friendly + ". Is there anything else I can help you with?"
            print("BOOKED:", location, friendly)
            conversation_history.append({"role": "assistant", "content": spoken})
            url = tts(spoken)
            if url:
                return Response(content=xml_play(url), media_type="application/xml")
        except Exception as e:
            print("ERROR:", e)

    if "GOODBYE" in answer:
        url = tts("Thank you for calling Union Recording Studio. Have a great day! Goodbye!")
        if url:
            return Response(content=xml_play(url, end=True), media_type="application/xml")
        return Response(content=xml_say("Goodbye!", end=True), media_type="application/xml")

    url = tts(answer)
    if url:
        return Response(content=xml_play(url), media_type="application/xml")
    return Response(content=xml_say(answer), media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

@app.post("/book-appointment")
async def book_appointment(request: Request):
    try:
        body = await request.json()
        print("RETELL BOOKING:", body)

        # Support both args and top-level
        args = body.get("args", body)
        patient_name = args.get("rider_name") or args.get("patient_name") or "Patient"
        date = args.get("appointment_date", "")
        time_raw = args.get("appointment_time", "")

        if not date or not time_raw:
            return {"status": "error", "message": "Missing date or time"}

        # Convert time to 24-hour format if needed (e.g. "9:00 AM" -> "09:00")
        import re
        time_raw = time_raw.strip()
        if "AM" in time_raw.upper() or "PM" in time_raw.upper():
            import datetime as dt
            try:
                t = dt.datetime.strptime(time_raw.upper().replace(".", ""), "%I:%M %p")
                time_24 = t.strftime("%H:%M")
            except:
                t = dt.datetime.strptime(time_raw.upper().replace(".", ""), "%I %p")
                time_24 = t.strftime("%H:%M")
        else:
            time_24 = time_raw

        service = get_calendar_service()
        event = {
            "summary": "Dental Appointment - " + patient_name,
            "start": {"dateTime": date + "T" + time_24 + ":00", "timeZone": "America/Los_Angeles"},
            "end": {"dateTime": date + "T" + time_24 + ":00", "timeZone": "America/Los_Angeles"},
        }
        result = service.events().insert(calendarId="primary", body=event).execute()
        print("BOOKED:", patient_name, date, time_24)

        return {
            "status": "success",
            "booking_id": result.get("id"),
            "patient_name": patient_name,
            "appointment_date": date,
            "appointment_time": time_24,
            "new_appointment_date": date,
            "new_appointment_time": time_raw
        }
    except Exception as e:
        print("BOOKING ERROR:", e)
        return {"status": "error", "message": str(e)}

@app.post("/fetch-appointment")
async def fetch_appointment(request: Request):
    try:
        body = await request.json()
        print("FETCH APPOINTMENT:", body)
        # For new patients or when no appointment exists
        return {
            "booking_found": False,
            "message": "No existing appointment found"
        }
    except Exception as e:
        print("FETCH ERROR:", e)
        return {"booking_found": False}
