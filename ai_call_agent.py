import json
import os
import time
import traceback
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Connect
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    Conversation,
    ConversationInitiationData,
)
from twilio_audio_interface import TwilioAudioInterface
from starlette.websockets import WebSocketDisconnect
from twilio.rest import Client

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# Render env mein sirf domain dena:
# project-api-phone-call-agent.onrender.com
NGROK_URL = os.getenv("NGROK_URL", "")

# Render env mein TARGET_PHONE_NUMBER bhi set kar sakte ho
TARGET_PHONE_NUMBER = "+923442862596"


def clean_domain(value: str) -> str:
    value = value.strip()
    value = value.replace("https://", "")
    value = value.replace("http://", "")
    value = value.replace("/", "")
    return value


NGROK_URL = clean_domain(NGROK_URL)

app = FastAPI()


class CustomerDetails(BaseModel):
    customer_name: str
    language: str


# ---------------- LIVE GAS READING MEMORY ----------------
latest_gas_reading = {
    "reading": None,
    "previous_reading": None,
    "level": "NO DEVICE READING YET",
    "trend": "UNKNOWN",
    "updated_at": 0.0,
}


def get_gas_level(reading: int) -> str:
    if reading < 100:
        return "SAFE"
    elif reading < 200:
        return "LOW GAS LEAK"
    elif reading < 300:
        return "MEDIUM GAS LEAK"
    else:
        return "HIGH / DANGEROUS GAS LEAK"


def get_trend(previous: Optional[int], current: int) -> str:
    if previous is None:
        return "UNKNOWN"
    if current > previous:
        return "INCREASING"
    if current < previous:
        return "DECREASING"
    return "STABLE"


def save_latest_reading(reading: int):
    previous = latest_gas_reading["reading"]

    latest_gas_reading["previous_reading"] = previous
    latest_gas_reading["reading"] = reading
    latest_gas_reading["level"] = get_gas_level(reading)
    latest_gas_reading["trend"] = get_trend(previous, reading)
    latest_gas_reading["updated_at"] = time.time()


def get_reading_age_seconds() -> Optional[int]:
    if latest_gas_reading["updated_at"] == 0:
        return None
    return int(time.time() - latest_gas_reading["updated_at"])


# ---------------- ROOT / DEBUG ----------------
@app.get("/")
async def root():
    return {
        "message": "Twilio-ElevenLabs Gas Sensor Server",
        "latest_reading": latest_gas_reading,
        "reading_age_seconds": get_reading_age_seconds(),
    }


@app.get("/latest-reading")
async def latest_reading_debug():
    return {
        "latest_reading": latest_gas_reading,
        "reading_age_seconds": get_reading_age_seconds(),
    }


# ---------------- ELEVENLABS SERVER TOOL ENDPOINT ----------------
@app.get("/elevenlabs/latest-gas-reading")
async def elevenlabs_latest_gas_reading():
    """
    ElevenLabs Server Tool will call this endpoint.
    This returns the latest live gas reading from Render memory.
    """
    reading = latest_gas_reading["reading"]

    print("🔧 ELEVENLABS SERVER TOOL HIT")
    print(f"🔧 Latest reading memory: {latest_gas_reading}")

    if reading is None:
        answer = (
            "Current gas reading is not available yet. "
            "No live reading has been received from the device."
        )

        print(f"🔧 TOOL ANSWER: {answer}")

        return {
            "answer": answer,
            "reading": None,
            "status": "NO DEVICE READING YET",
            "trend": "UNKNOWN",
            "age_seconds": None,
        }

    age = get_reading_age_seconds()

    answer = (
        f"Current gas reading is {reading}. "
        f"Status is {latest_gas_reading['level']}. "
        f"Trend is {latest_gas_reading['trend']}. "
        f"Last updated {age} seconds ago."
    )

    print(f"🔧 TOOL ANSWER: {answer}")

    return {
        "answer": answer,
        "reading": reading,
        "status": latest_gas_reading["level"],
        "trend": latest_gas_reading["trend"],
        "age_seconds": age,
    }


# ---------------- TWILIO INBOUND / ANSWERED CALL ----------------
@app.post("/twilio/inbound_call")
async def handle_incoming_call(request: Request):
    customer_name = request.query_params.get("CustomerName", "Sarim")
    language = request.query_params.get("Language", "en")

    form_data = await request.form()
    call_sid = form_data.get("CallSid", "Unknown")
    from_number = form_data.get("From", "Unknown")

    print(
        f"Incoming or answered outbound call: CallSid={call_sid}, "
        f"From={from_number}, CustomerName={customer_name}, Language={language}"
    )

    if not NGROK_URL:
        raise HTTPException(
            status_code=500,
            detail="NGROK_URL / Render domain is missing in environment variables.",
        )

    response = VoiceResponse()
    connect = Connect()

    connect.stream(
        url=f"wss://{NGROK_URL}/media-stream-eleven/{customer_name}/{language}"
    )

    response.append(connect)

    return HTMLResponse(content=str(response), media_type="application/xml")


# ---------------- ELEVENLABS MEDIA STREAM ----------------
@app.websocket("/media-stream-eleven/{customer_name}/{language}")
async def handle_media_stream(websocket: WebSocket, customer_name: str, language: str):
    await websocket.accept()
    print(f"WebSocket connection opened for {customer_name} in {language} language.")

    audio_interface = TwilioAudioInterface(websocket)
    eleven_labs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

    # IMPORTANT:
    # Yahan old reading inject nahi karni.
    # Warna AI 126/145/183 ko memory bana ke repeat karega.
    conversation_override = {
        "agent": {
            "prompt": {
                "prompt": (
                    "You are Ahmed, a smart home gas safety assistant. "
                    "Speak in clear English only. Be serious, concise, and direct. "

                    "A gas leak alert was triggered in Sarim's house. "
                    "The first alert reading may become old within seconds, so never treat "
                    "the first alert reading as the current reading forever. "

                    "CRITICAL RULE: Whenever the user asks for the current gas reading, "
                    "latest gas reading, gas rating, gas level, gas pressure, current sensor value, "
                    "current status, whether it is safe, whether gas is increasing or decreasing, "
                    "you MUST call the getLatestGasReading server tool first. "

                    "Do not answer current reading questions from memory. "
                    "Do not use any old reading from the start of the call. "
                    "Do not say 'I am fetching', 'let me check', 'one moment', "
                    "'I will fetch', or similar filler. "
                    "Do not say the tool is unavailable. "

                    "After the getLatestGasReading server tool returns, immediately speak the answer field directly. "
                    "Example: 'Current gas reading is 57. Status is SAFE. Trend is STABLE.' "

                    "If the tool says SAFE, tell the user the current environment is safe. "
                    "If the tool says LOW GAS LEAK, MEDIUM GAS LEAK, or HIGH / DANGEROUS GAS LEAK, "
                    "warn the user clearly and give safety instructions. "

                    "Safety instructions: avoid electrical switches, open windows if possible, "
                    "turn off the gas supply if safe, and leave the area carefully."
                )
            },
            "first_message": (
                f"Hello {customer_name}, Ahmed speaking. A gas leak alert was triggered in your house. "
                "Please avoid electrical switches, open windows if possible, and leave the area carefully. "
                "When you ask me for the current gas reading, I will check the live sensor reading and tell you directly."
            ),
            "language": "en",
        },
        "tts": {
            "model_id": "eleven_multilingual_v2",
            "voice_id": "6u6JbqKdaQy89ENzLSju",
            "output_format": "ulaw_8000",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.7,
            },
        },
    }

    config = ConversationInitiationData(
        conversation_config_override=conversation_override
    )

    conversation_log = []

    def on_agent_response(text: str):
        print(f"Agent: {text}")
        conversation_log.append({"speaker": "agent", "message": text})

    def on_user_transcript(text: str):
        print(f"User: {text}")
        conversation_log.append({"speaker": "user", "message": text})

    conversation = None

    try:
        conversation = Conversation(
            client=eleven_labs_client,
            agent_id=ELEVENLABS_AGENT_ID,
            requires_auth=False,
            audio_interface=audio_interface,
            config=config,
            callback_agent_response=on_agent_response,
            callback_user_transcript=on_user_transcript,
        )

        conversation.start_session()
        print("Conversation session started.")

        async for message in websocket.iter_text():
            if message:
                await audio_interface.handle_twilio_message(json.loads(message))

    except WebSocketDisconnect:
        print("WebSocket disconnected.")

    except Exception as e:
        print(f"Error in WebSocket handler: {e}")
        traceback.print_exc()

    finally:
        if conversation is not None:
            try:
                conversation.end_session()
                conversation.wait_for_session_end()
                print("Conversation session ended.")
            except Exception as e:
                print(f"Error ending conversation session: {e}")
                traceback.print_exc()


# ---------------- CALL STATE ----------------
call_state = {
    "is_active": False,
    "last_call_time": 0.0,
    "last_success_time": 0.0,
}


@app.post("/twilio/outbound_call")
async def make_outbound_call(customer_name: str, language: str, number: str):
    if not number:
        raise HTTPException(status_code=400, detail="Target number is required.")

    if not NGROK_URL:
        raise HTTPException(
            status_code=500,
            detail="NGROK_URL / Render domain is missing in environment variables.",
        )

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        raise HTTPException(
            status_code=500,
            detail="Twilio credentials or Twilio phone number are missing.",
        )

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    try:
        redirect_url = (
            f"https://{NGROK_URL}/twilio/inbound_call"
            f"?CustomerName={customer_name}&Language={language}"
        )

        status_callback_url = f"https://{NGROK_URL}/twilio/call-status"

        twiml_response = VoiceResponse()
        twiml_response.redirect(redirect_url, method="POST")

        call = client.calls.create(
            twiml=str(twiml_response),
            to=number,
            from_=TWILIO_PHONE_NUMBER,
            status_callback=status_callback_url,
            status_callback_event=[
                "completed",
                "busy",
                "no-answer",
                "failed",
                "canceled",
            ],
        )

        call_state["is_active"] = True
        call_state["last_call_time"] = time.time()

        print(f"Outbound call initiated: {call.sid}")

        return {
            "message": "Outbound call initiated",
            "CallSid": call.sid,
            "to": number,
            "from": TWILIO_PHONE_NUMBER,
            "latest_reading": latest_gas_reading,
        }

    except Exception as e:
        print(f"Error initiating outbound call: {e}")
        call_state["is_active"] = False
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/twilio/call-status")
async def call_status_webhook(request: Request):
    form_data = await request.form()
    call_status = form_data.get("CallStatus")

    print(f"Twilio Call Status Update: {call_status}")

    if call_status == "completed":
        call_state["last_success_time"] = time.time()
        call_state["is_active"] = False
        print("✅ Call Completed Successfully.")

    elif call_status in ["busy", "no-answer", "failed", "canceled"]:
        call_state["is_active"] = False
        print("❌ Call Failed/Missed. System ready for retry.")

    return {"status": "ok"}


# ---------------- NODEMCU ENDPOINTS ----------------
@app.api_route("/update-reading", methods=["GET", "POST"])
async def update_reading(reading: int):
    """
    NodeMCU sends live gas reading here.
    Example: /update-reading?reading=57
    """
    save_latest_reading(reading)

    print(
        f"📡 Live gas reading updated from device: {reading} | "
        f"Level: {latest_gas_reading['level']} | "
        f"Trend: {latest_gas_reading['trend']}"
    )

    return {
        "status": "updated",
        "reading": latest_gas_reading["reading"],
        "previous_reading": latest_gas_reading["previous_reading"],
        "level": latest_gas_reading["level"],
        "trend": latest_gas_reading["trend"],
        "updated_at": latest_gas_reading["updated_at"],
        "reading_age_seconds": get_reading_age_seconds(),
    }


@app.get("/call-status")
async def call_status_for_nodemcu():
    """
    NodeMCU checks whether call is active.
    """
    return {
        "active": call_state["is_active"],
        "latest_reading": latest_gas_reading["reading"],
        "previous_reading": latest_gas_reading["previous_reading"],
        "level": latest_gas_reading["level"],
        "trend": latest_gas_reading["trend"],
        "updated_at": latest_gas_reading["updated_at"],
        "reading_age_seconds": get_reading_age_seconds(),
    }


@app.get("/trigger-gas-alert")
async def trigger_gas_alert(reading: Optional[int] = None):
    """
    NodeMCU triggers gas alert call here.
    Example: /trigger-gas-alert?reading=180
    """
    current_time = time.time()

    if reading is not None:
        save_latest_reading(reading)
        print(
            f"⚠️ Alert reading received from device: {reading} | "
            f"Level: {latest_gas_reading['level']} | "
            f"Trend: {latest_gas_reading['trend']}"
        )

    if latest_gas_reading["reading"] is None:
        return {
            "status": "error",
            "reason": "no_device_reading_received_yet",
            "message": "Send /update-reading?reading=VALUE or /trigger-gas-alert?reading=VALUE from NodeMCU first.",
        }

    if not TARGET_PHONE_NUMBER:
        raise HTTPException(
            status_code=500,
            detail="TARGET_PHONE_NUMBER is missing in Render environment variables.",
        )

    if call_state["is_active"]:
        return {
            "status": "ignored",
            "reason": "call_in_progress",
            "reading": latest_gas_reading["reading"],
            "level": latest_gas_reading["level"],
            "trend": latest_gas_reading["trend"],
        }

    if (current_time - call_state["last_success_time"]) < 10:
        return {
            "status": "ignored",
            "reason": "already_acknowledged",
            "reading": latest_gas_reading["reading"],
            "level": latest_gas_reading["level"],
            "trend": latest_gas_reading["trend"],
        }

    if (current_time - call_state["last_call_time"]) < 30:
        return {
            "status": "ignored",
            "reason": "cooldown_active",
            "reading": latest_gas_reading["reading"],
            "level": latest_gas_reading["level"],
            "trend": latest_gas_reading["trend"],
        }

    print(
        f"⚠️ GAS ALERT RECEIVED! Calling with live device reading: "
        f"{latest_gas_reading['reading']} | "
        f"{latest_gas_reading['level']} | "
        f"{latest_gas_reading['trend']}"
    )

    return await make_outbound_call(
        customer_name="Sarim",
        language="en",
        number=TARGET_PHONE_NUMBER,
    )
