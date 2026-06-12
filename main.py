from fastapi import FastAPI, Request
from fastapi.responses import Response
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os
import uvicorn
import datetime

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SCOPES = ["https://www.googleapis.com/auth/calendar"]

conversation_history = []

def get_calendar_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

app = FastAPI()

@app.post("/incoming-call")
async def incoming_call(request: Request):
    global conversation_history
    conversation_history = []
    print("=== NEW CALL ===")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="auto">'
        '<Say voice="alice" language="en-US">Hello! You have reached our clinic. How can I help you?</Say>'
        "</Gather>"
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")

@app.post("/handle-speech")
async def handle_speech(request: Request):
    global conversation_history
    form = await request.form()
    user_said = form.get("SpeechResult", "")
    print("PATIENT:", user_said)
    today = datetime.date.today().strftime("%Y-%m-%d")
    year = datetime.date.today().year

    prompt = (
        "You are a clinic receptionist. Today is " + today + ". Year is always " + str(year) + ". "
        "When patient gives a date and time, assume year " + str(year) + " automatically - never ask for the year. "
        "Convert any time to 24-hour format for the BOOK line. Examples: 4pm=16:00, 7pm=19:00, 10am=10:00. "
        "As soon as you understand a date and time, output ONLY: BOOK: YYYY-MM-DD HH:MM "
        "No other words. No confirmation. No questions about year."
    )

    conversation_history.append({"role": "user", "content": user_said})

    result = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": prompt}] + conversation_history,
    )

    answer = result.choices[0].message.content.strip()
    print("BOT:", answer)
    conversation_history.append({"role": "assistant", "content": answer})

    if "BOOK:" in answer:
        try:
            book_part = answer.split("BOOK:")[1].strip().split()
            date = book_part[0]
            time = book_part[1]
            service = get_calendar_service()
            event = {
                "summary": "Clinic Appointment",
                "start": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
                "end": {"dateTime": date + "T" + time + ":00", "timeZone": "America/Los_Angeles"},
            }
            service.events().insert(calendarId="primary", body=event).execute()
            dt = datetime.datetime.strptime(date + " " + time, "%Y-%m-%d %H:%M")
            friendly_time = dt.strftime("%B %d at %I:%M %p")
            spoken = "Perfect! Your appointment is booked for " + friendly_time + ". See you then! Goodbye!"
            print("BOOKED:", friendly_time)
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                '<Say voice="alice" language="en-US">' + spoken + "</Say>"
                "</Response>"
            )
            return Response(content=xml, media_type="application/xml")
        except Exception as e:
            print("ERROR:", e)
            answer = "Sorry, I could not book the appointment. Please call back."

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Say voice="alice" language="en-US">' + answer + "</Say>"
        '<Gather input="speech" action="/handle-speech" language="en-US" timeout="10" speechTimeout="auto"></Gather>'
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
